import math

import pytest

from tekken_vod_helper.recording_clock import Checkpoint, RecordingClock, RecordingClockError, map_source_time


def point(t,d,**kwargs):
    return Checkpoint(t,d,uncertainty_seconds=0,**kwargs)


def test_continuous_recording_maps_with_calibration_bounds():
    clock=RecordingClock('take1',[Checkpoint(100,0,uncertainty_seconds=.1),Checkpoint(110,10,uncertainty_seconds=.1)])
    result=clock.map_epoch(105)
    assert result['lower_seconds']==pytest.approx(4.9)
    assert result['upper_seconds']==pytest.approx(5.1)


def test_pause_location_between_checkpoints_stays_an_interval():
    clock=RecordingClock('take1',[point(100,0),point(110,6,paused=True)])
    result=clock.map_epoch(105)
    assert result['lower_seconds']==1 and result['upper_seconds']==5
    assert result['pause_transition']


def test_paused_events_keep_the_same_media_time():
    clock=RecordingClock('take1',[point(100,4,paused=True),point(110,4,paused=True)])
    result=clock.map_epoch(105)
    assert result['status']=='paused' and result['estimate_seconds']==4


def test_paused_endpoints_can_hide_a_resume_and_pause():
    clock=RecordingClock('take1',[point(100,4,paused=True),point(110,10,paused=True)])
    result=clock.map_epoch(105)
    assert result['status']=='mapped' and result['lower_seconds']==5 and result['upper_seconds']==9


def test_unknown_capture_latency_is_not_replaced_by_zero():
    result=RecordingClock('take1',[Checkpoint(100,0),Checkpoint(110,10)]).map_epoch(105)
    assert result['status']=='unknown_uncertainty' and 'estimate_seconds' not in result


def test_no_extrapolation_or_mapping_across_stop():
    clock=RecordingClock('take1',[point(100,0),point(110,10),point(115,10,active=False)])
    assert clock.map_epoch(99)['reason']=='outside_checkpoint_range'
    assert clock.map_epoch(116)['reason']=='outside_checkpoint_range'
    assert clock.map_epoch(112)['reason']=='inactive_or_stop_transition'
    assert clock.map_epoch(110)['estimate_seconds']==10


@pytest.mark.parametrize('points',[
    [point(100,0),point(100,0)], [point(110,0),point(100,1)],
    [point(100,10),point(110,0)], [point(100,0),point(110,11)],
    [point(100,0,active=False),point(110,0)]])
def test_bad_clocks_and_restarts_require_review(points):
    with pytest.raises(RecordingClockError):RecordingClock('take1',points)


@pytest.mark.parametrize('value',[math.nan,math.inf,-math.inf,True,'bad'])
def test_nonfinite_or_invalid_clock_values_are_rejected(value):
    with pytest.raises(RecordingClockError):Checkpoint(value,0)


def test_source_offset_sign_bounds_and_start_guard():
    mapping=RecordingClock('take1',[point(100,0),point(110,10)]).map_epoch(105)
    result=map_source_time(mapping,2,.1)
    assert result['lower_seconds']==pytest.approx(2.9)
    assert result['upper_seconds']==pytest.approx(3.1)
    assert map_source_time(mapping,6,0)['reason']=='before_source_start'
    assert map_source_time(mapping,5,.1)['reason']=='straddles_source_start'
    assert map_source_time(mapping,2)['status']=='unknown_uncertainty'
    assert 'estimate_seconds' not in map_source_time(mapping,2)


def test_independent_source_pause_behavior_requires_evidence():
    clock=RecordingClock('take1',[point(100,0),point(110,6,paused=True),point(120,16)])
    mapping=clock.map_epoch(115)
    assert map_source_time(mapping,0,0)['reason']=='independent_source_pause_behavior_unverified'
    result=map_source_time(mapping,0,0,shares_main_pause_clock=True)
    assert result['lower_seconds']==11


def test_mapper_cli_preserves_events_and_refuses_existing_output(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    checkpoints=tmp_path/'checkpoints.json';history=tmp_path/'history.jsonl';out=tmp_path/'mapped.json'
    checkpoints.write_text(json.dumps({'recordings':{'take1':[{'epoch':100,'duration_seconds':0,'uncertainty_seconds':0},{'epoch':110,'duration_seconds':10,'uncertainty_seconds':0}]},'source_offsets':{'camera':{'seconds':2,'uncertainty_seconds':.1}}}))
    event={'epoch':105,'p1_name':'Example','p1_score':2}
    history.write_text(json.dumps(event)+'\n')
    command=[sys.executable,str(root/'scripts/map_overlay_history.py'),str(checkpoints),str(history),str(out),'--recording-id','take1']
    result=subprocess.run(command,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    report=json.loads(out.read_text())
    assert report['events'][0]['event']==event
    assert report['events'][0]['sources']['camera']['estimate_seconds']==3
    saved=out.read_bytes()
    assert subprocess.run(command,capture_output=True).returncode==1
    assert out.read_bytes()==saved
    assert json.loads(history.read_text())==event
