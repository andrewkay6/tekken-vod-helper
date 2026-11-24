import hashlib
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest
from tekken_vod_helper import recording_media as media


def document(stamps, rate='60/1'):
    return {'format':{'format_name':'matroska,webm'},'streams':[{'index':0,'codec_type':'video','codec_name':'hevc','time_base':'1/1000','r_frame_rate':rate,'avg_frame_rate':rate}], 'packets':[{'stream_index':0,'pts':x} for x in stamps]}


def test_cadence_uses_pts_in_presentation_order_not_b_frame_packet_order():
    stamps=[round(Fraction(i*1001,30)) for i in range(30)]
    stamps[2:5]=reversed(stamps[2:5])
    result=media.cadence(document(stamps),'30000/1001')
    assert result['matches'] and result['all_slots_present']


def test_cadence_reports_an_existing_gap_without_erasing_or_claiming_its_cause():
    stamps=[round(Fraction(i*1001,30)) for i in range(31) if i!=1]
    result=media.cadence(document(stamps),'30000/1001')
    assert result['matches'] and result['unfilled_frame_slots']==1
    assert not result['all_slots_present']


def test_wrong_intended_rate_and_off_grid_timing_are_not_a_metadata_fix():
    stamps=[round(Fraction(i*1001,30)) for i in range(30)]
    assert not media.cadence(document(stamps),'60000/1001')['matches']
    stamps[10]+=9
    assert not media.cadence(document(stamps),'30000/1001')['matches']


@pytest.mark.parametrize('value',['0','-30','nan','30/0','wat'])
def test_intended_rate_must_be_explicit_and_positive(value):
    with pytest.raises(media.RecordingMediaError):media.frame_rate(value)


def test_packet_comparison_ignores_interleaving_but_keeps_each_stream_pts_and_payload():
    d=document([0,33,67])
    d['streams'].append({'index':1,'codec_type':'audio','codec_name':'aac','time_base':'1/1000'})
    d['packets'].insert(1,{'stream_index':1,'pts':-21,'data_hash':'SHA256:audio'})
    expected=media._packet_signatures(d)
    d['packets'].append(d['packets'].pop(1))
    assert media._packet_signatures(d)==expected
    d['packets'][-1]['pts']=0
    assert media._packet_signatures(d)!=expected


@pytest.fixture(scope='module')
def malformed_camera(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):pytest.skip('FFmpeg tools unavailable')
    root=tmp_path_factory.mktemp('camera-metadata')
    source=root/'correct.mkv';broken=root/'wrong-header.mkv'
    command=['ffmpeg','-v','error','-nostdin','-f','lavfi','-i','testsrc2=s=128x72:r=30000/1001','-f','lavfi','-i','sine=frequency=700:sample_rate=48000','-t','1.1011','-c:v','libx265','-x265-params','pools=none:frame-threads=1:bframes=0:log-level=error','-c:a','aac','-avoid_negative_ts','disabled',str(source)]
    r=subprocess.run(command,capture_output=True,text=True)
    if r.returncode and 'Unknown encoder' in r.stderr:pytest.skip('libx265 unavailable')
    assert r.returncode==0,r.stderr
    subprocess.run(['ffmpeg','-v','error','-nostdin','-copyts','-i',str(source),'-map','0','-c','copy','-r','60000/1001','-bsf:v','hevc_metadata=tick_rate=60000/1001','-avoid_negative_ts','disabled',str(broken)],check=True,capture_output=True)
    return source,broken


def test_real_repair_preserves_audio_pts_pictures_and_original(malformed_camera,tmp_path):
    source,broken=malformed_camera
    fingerprint=hashlib.sha256(broken.read_bytes()).hexdigest()
    assert media.inspect_recording(broken,'30000/1001')['needs_repair']
    output=tmp_path/'fixed.mkv'
    receipt=media.repair_recording(broken,output,'30000/1001')
    assert output.exists() and Path(str(output)+'.metadata-repair.json').exists()
    assert receipt['presentation_timestamps_preserved'] and receipt['non_video_payloads_preserved']
    assert receipt['decoded_pictures_preserved'] and receipt['decoded_pictures']['frames']>=30
    assert not media.inspect_recording(output,'30000/1001')['needs_repair']
    assert hashlib.sha256(broken.read_bytes()).hexdigest()==fingerprint


def test_existing_output_or_receipt_is_never_overwritten(malformed_camera,tmp_path):
    _,source=malformed_camera;out=tmp_path/'fixed.mkv';out.write_bytes(b'keep')
    with pytest.raises(media.RecordingMediaError,match='overwrite'):media.repair_recording(source,out,'30000/1001')
    assert out.read_bytes()==b'keep'
    out.unlink();receipt=Path(str(out)+'.metadata-repair.json');receipt.write_text('keep receipt')
    with pytest.raises(media.RecordingMediaError,match='overwrite'):media.repair_recording(source,out,'30000/1001')
    assert not out.exists() and receipt.read_text()=='keep receipt'


def test_validation_failure_does_not_publish_any_copy(malformed_camera,tmp_path,monkeypatch):
    _,source=malformed_camera;out=tmp_path/'fixed.mkv'
    values=iter([{'frames':33,'hash':'one'},{'frames':33,'hash':'different'}])
    monkeypatch.setattr(media,'_pictures',lambda *_:next(values))
    with pytest.raises(media.RecordingMediaError,match='pictures changed'):media.repair_recording(source,out,'30000/1001')
    assert not out.exists() and not Path(str(out)+'.metadata-repair.json').exists()
    assert not list(tmp_path.glob('.tvh-metadata-*'))


def test_receipt_publication_race_preserves_the_other_file(malformed_camera,tmp_path,monkeypatch):
    _,source=malformed_camera;out=tmp_path/'fixed.mkv';sidecar=Path(str(out)+'.metadata-repair.json')
    original=media.os.link;calls=[]
    def race(a,b):
        calls.append(b)
        if len(calls)==2:sidecar.write_text('another writer')
        return original(a,b)
    monkeypatch.setattr(media.os,'link',race)
    with pytest.raises(FileExistsError):media.repair_recording(source,out,'30000/1001')
    assert not out.exists() and sidecar.read_text()=='another writer'


def test_half_frame_phase_that_ffmpeg_rounds_is_rejected(malformed_camera,tmp_path):
    _,source=malformed_camera
    shifted=tmp_path/'shifted.mkv';out=tmp_path/'fixed.mkv'
    subprocess.run(['ffmpeg','-v','error','-nostdin','-copyts','-itsoffset','0.017','-i',str(source),'-map','0','-c','copy','-avoid_negative_ts','disabled',str(shifted)],check=True,capture_output=True)
    with pytest.raises(media.RecordingMediaError):media.repair_recording(shifted,out,'30000/1001')
    assert not out.exists()
