"""Frame-accurate cut without re-encoding the whole clip.

The stream-copy export starts each clip on the keyframe at or before the requested start (keyframes
are ~4.17 s apart in these masters), so clips began up to 4.2 s early. This re-encodes only
[start, first keyframe after start) with libx264 (CRF 12, separate SPS/PPS id so decoders never mix
the two parameter sets), stream-copies [keyframe, end), joins them as MPEG-TS on the master's own timestamps (-copyts), then muxes audio
stream-copied from the exact start.

usage: smartcut.py MASTER START END OUT.mp4
"""
import json,subprocess,sys,tempfile
from pathlib import Path
FF=['ffmpeg','-hide_banner','-loglevel','error','-y']

def keyframes(master,start):
    out=subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-skip_frame','nokey','-show_entries',
        'frame=pts_time','-of','csv=p=0','-read_intervals','%.3f%%+30'%max(0,start-12),master]).decode().split()
    return sorted(float(x) for x in out if x.strip())

def frames_between(master,a,b):
    out=subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','packet=pts_time',
        '-of','csv=p=0','-read_intervals','%.3f%%+%.3f'%(max(0,a-6),b-a+12),master]).decode().split()
    return sum(1 for x in out if x.strip() and a<=float(x.strip(','))<b-0.0005)

def cut(master,start,end,out):
    ks=keyframes(master,start)
    on=[k for k in ks if abs(k-start)<0.002]
    k=on[0] if on else min(x for x in ks if x>start)
    out=Path(out); tmp=Path(tempfile.mkdtemp(prefix='smartcut-',dir=out.parent))
    parts=[]
    if not on:
        head=tmp/'head.ts'
        # every master frame in [start, k), counted, so the head ends exactly where the copied GOP begins
        n=frames_between(master,start,k)
        subprocess.run(FF+['-ss','%.6f'%start,'-i',master,'-copyts','-frames:v',str(n),'-an','-sn','-dn','-map','0:v:0',
            '-c:v','libx264','-preset','slow','-crf','12','-profile:v','high','-pix_fmt','yuv420p',
            '-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-color_range','tv',
            '-x264-params','sps-id=1:keyint=600','-fps_mode','passthrough','-f','mpegts','-muxdelay','0','-muxpreload','0',str(head)],check=True)
        parts.append(head)
    tail=tmp/'tail.ts'
    subprocess.run(FF+['-ss','%.6f'%(k+0.001),'-t','%.6f'%(end-k),'-i',master,'-copyts','-an','-sn','-dn','-map','0:v:0',
        '-c:v','copy','-bsf:v','h264_mp4toannexb','-f','mpegts','-muxdelay','0','-muxpreload','0',str(tail)],check=True)
    parts.append(tail)
    # both parts carry the master's own timestamps, so a byte-level join is continuous
    video=tmp/'video.ts'
    with open(video,'wb') as w:
        for part in parts: w.write(part.read_bytes())
    subprocess.run(FF+['-i',str(video),'-ss','%.6f'%start,'-i',master,'-map','0:v:0','-map','1:a:0','-t','%.6f'%(end-start),
        '-c','copy','-movflags','+faststart',str(out)],check=True)
    for p in tmp.iterdir(): p.unlink()
    tmp.rmdir()
    return {'start':start,'end':end,'keyframe':k,'reencoded_head_s':0 if on else round(k-start,4)}

if __name__=='__main__':
    print(json.dumps(cut(sys.argv[1],float(sys.argv[2]),float(sys.argv[3]),sys.argv[4])))
