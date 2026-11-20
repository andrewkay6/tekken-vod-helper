"""Replace exported stream-copy clips with frame-exact smart cuts, one clip at a time.

usage: recut_smartcut.py EXPORT_DIR MASTER N [N ...]   (run after the export pass; BB10 2026-11-13, BB11 2026-11-20)
Reads each clip's youtube.json (start/end/match_index), cuts to a temp file beside the clip, checks
duration, audio and a clean decode of the joined region, then atomically replaces the clip and writes
<clip>.smartcut.json. Skips a clip whose upload already finished.
"""
import glob,json,os,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import smartcut
exp,master=Path(sys.argv[1]),sys.argv[2]; want={int(x) for x in sys.argv[3:]}
manifest=json.loads((exp/'output'/'_youtube_uploads'/'manifest.json').read_text())['entries']
for e in manifest:
    y=json.loads(Path(e['metadata_path']).read_text()); i=y['match_index']
    if i not in want: continue
    job=exp/'upload'/'jobs'/e['upload_id']
    if (job/'thumbnail-set.json').exists(): print(i,'already uploaded; skipped',flush=True); continue
    clip=Path(e['video_path']); tmp=clip.with_name(clip.stem+'.smartcut-tmp.mp4')
    r=smartcut.cut(master,y['start'],y['end'],str(tmp))
    pr=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','stream=codec_type,duration','-of','json',str(tmp)]))
    kinds={s['codec_type']:float(s['duration']) for s in pr['streams']}
    want_d=y['end']-y['start']
    assert set(kinds)=={'video','audio'},kinds
    assert abs(kinds['video']-want_d)<0.1 and abs(kinds['audio']-want_d)<0.1,(kinds,want_d)
    err=subprocess.run(['ffmpeg','-v','error','-t','%.3f'%(r['reencoded_head_s']+5),'-i',str(tmp),'-f','null','-'],capture_output=True,text=True).stderr
    assert not err.strip(),err
    os.replace(tmp,clip)
    r.update(video_s=round(kinds['video'],3),audio_s=round(kinds['audio'],3),requested_s=round(want_d,3),
             replaces='stream-copy clip that started on the keyframe at or before start')
    clip.with_name(clip.name.replace('.mp4','.smartcut.json')).write_text(json.dumps(r,indent=2)+'\n')
    print(i,'ok',json.dumps(r),flush=True)
