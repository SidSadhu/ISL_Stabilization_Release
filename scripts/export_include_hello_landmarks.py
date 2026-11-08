#!/usr/bin/env python3
"""Export actual MediaPipe landmark overlays for the official INCLUDE Hello clip.

Runs the user's installed MediaPipe/OpenCV on the original clip in frame order.
Outputs annotated original frames, enlarged numbered hand-landmark crops, and JSON.
The pose display is the first 25 current full-pose landmarks; this is NOT claimed to
be detector-identical to AI4Bharat's historical upper_body_only=True model.
"""
from __future__ import annotations
import argparse, json, math, zipfile
from pathlib import Path
import cv2
import mediapipe as mp
import numpy as np
from PIL import Image, ImageDraw, ImageFont

TIMES = [0.00, 0.40, 0.64, 0.84, 1.08, 1.28, 1.52, 1.96, 2.40]

def load_font(size: int, bold: bool = False):
    candidates = [
        Path(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for p in candidates:
        if p.exists(): return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()

def draw_pose_upper25(img, pose_landmarks):
    if pose_landmarks is None: return False
    pts = pose_landmarks.landmark[:25]
    h, w = img.shape[:2]
    for a, b in mp.solutions.pose.POSE_CONNECTIONS:
        if a < 25 and b < 25:
            pa, pb = pts[a], pts[b]
            if pa.visibility > 0.25 and pb.visibility > 0.25:
                p1=(int(pa.x*w),int(pa.y*h)); p2=(int(pb.x*w),int(pb.y*h))
                cv2.line(img,p1,p2,(255,170,40),3,cv2.LINE_AA)
    for i,p in enumerate(pts):
        if p.visibility > 0.25:
            xy=(int(p.x*w),int(p.y*h))
            cv2.circle(img,xy,4,(255,210,80),-1,cv2.LINE_AA)
    return True

def draw_hand(img, hand, numbered=False, scale=1.0):
    h,w=img.shape[:2]; pts=[]
    for p in hand.landmark:
        pts.append((int(p.x*w),int(p.y*h)))
    for a,b in mp.solutions.hands.HAND_CONNECTIONS:
        cv2.line(img,pts[a],pts[b],(50,220,120),max(2,int(3*scale)),cv2.LINE_AA)
    for i,(x,y) in enumerate(pts):
        cv2.circle(img,(x,y),max(3,int(5*scale)),(255,255,255),-1,cv2.LINE_AA)
        cv2.circle(img,(x,y),max(2,int(3*scale)),(25,95,65),-1,cv2.LINE_AA)
        if numbered:
            cv2.putText(img,str(i),(x+5,y-5),cv2.FONT_HERSHEY_SIMPLEX,0.42*scale,(20,20,20),3,cv2.LINE_AA)
            cv2.putText(img,str(i),(x+5,y-5),cv2.FONT_HERSHEY_SIMPLEX,0.42*scale,(255,255,255),1,cv2.LINE_AA)
    return pts

def crop_around_hand(frame, hand, pad=0.36):
    h,w=frame.shape[:2]
    xs=[int(p.x*w) for p in hand.landmark]; ys=[int(p.y*h) for p in hand.landmark]
    x1,x2=min(xs),max(xs); y1,y2=min(ys),max(ys)
    bw=max(1,x2-x1); bh=max(1,y2-y1)
    x1=max(0,int(x1-bw*pad)); x2=min(w,int(x2+bw*pad))
    y1=max(0,int(y1-bh*pad)); y2=min(h,int(y2+bh*pad))
    crop=frame[y1:y2,x1:x2].copy()
    # Remap original hand points into crop coordinates for a true, enlarged overlay.
    ch,cw=crop.shape[:2]
    proxy=type("Hand",(),{})()
    proxy.landmark=[]
    for p in hand.landmark:
        q=type("Point",(),{})()
        q.x=(p.x*w-x1)/max(1,cw); q.y=(p.y*h-y1)/max(1,ch)
        proxy.landmark.append(q)
    draw_hand(crop,proxy,numbered=True,scale=1.25)
    return crop

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--video',required=True,type=Path)
    ap.add_argument('--output-dir',required=True,type=Path)
    args=ap.parse_args()
    video=args.video.expanduser().resolve(); out=args.output_dir.expanduser().resolve()
    if not video.is_file(): raise FileNotFoundError(video)
    out.mkdir(parents=True,exist_ok=True)
    cap=cv2.VideoCapture(str(video))
    if not cap.isOpened(): raise RuntimeError(f'Cannot open video: {video}')
    fps=float(cap.get(cv2.CAP_PROP_FPS) or 0)
    total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    targets={int(round(t*fps)):t for t in TIMES}
    hands=mp.solutions.hands.Hands(static_image_mode=False,max_num_hands=2,min_detection_confidence=0.5,min_tracking_confidence=0.5)
    pose=mp.solutions.pose.Pose(static_image_mode=False,min_detection_confidence=0.5,min_tracking_confidence=0.5,model_complexity=1)
    selected=[]; frame_data=[]; frame_idx=0
    try:
        while True:
            ok,bgr=cap.read()
            if not ok: break
            rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
            hr=hands.process(rgb); pr=pose.process(rgb)
            if frame_idx in targets:
                annotated=bgr.copy()
                pose_found=draw_pose_upper25(annotated,pr.pose_landmarks)
                raw_hands=getattr(hr,'multi_hand_landmarks',None) or []
                hand_points=[]
                for hand in raw_hands:
                    hand_points.append(draw_hand(annotated,hand,numbered=False))
                selected.append((targets[frame_idx],frame_idx,annotated,bgr.copy(),raw_hands,pr.pose_landmarks))
                frame_data.append({
                    'time_seconds':targets[frame_idx], 'frame_index':frame_idx,
                    'pose_detected':bool(pose_found), 'hand_count':len(raw_hands),
                    'hands':[{'landmarks_21':[{'index':i,'x_normalized':float(p.x),'y_normalized':float(p.y)} for i,p in enumerate(hand.landmark)]} for hand in raw_hands],
                })
            frame_idx+=1
    finally:
        cap.release(); hands.close(); pose.close()
    if not selected: raise RuntimeError('No target frames were extracted; check video duration/fps.')

    # Composite annotated full-frame sequence, exact frames from source video.
    cols=3; card_w=620; img_h=349; header=48; footer=28; gap=22; margin=32
    rows=math.ceil(len(selected)/cols)
    canvas=Image.new('RGB',(margin*2+cols*card_w+(cols-1)*gap, 150+rows*(header+img_h+footer+gap)),'#F3F6F7')
    d=ImageDraw.Draw(canvas)
    d.text((margin,24),'OFFICIAL INCLUDE HELLO · ACTUAL MEDIAPIPE OVERLAYS',font=load_font(28,True),fill='#153246')
    d.text((margin,68),f'{video.name} · {width}×{height} · {fps:.2f} FPS · {total} frames',font=load_font(16),fill='#536975')
    d.text((margin,96),'Blue/orange: upper-body pose points 0–24 · Green: detected hand skeleton · All shown from the current local MediaPipe runtime',font=load_font(14),fill='#536975')
    for i,(t,idx,ann,orig,raw_hands,pose_lms) in enumerate(selected):
        r=i//cols; c=i%cols; x=margin+c*(card_w+gap); y=136+r*(header+img_h+footer+gap)
        d.rounded_rectangle((x,y,x+card_w,y+header+img_h+footer),radius=13,fill='white',outline='#D9E2E6',width=2)
        d.text((x+14,y+10),f'FRAME {idx}',font=load_font(15,True),fill='#176B61')
        d.text((x+card_w-100,y+10),f'{t:.2f}s',font=load_font(15,True),fill='#344E5B')
        rgb=cv2.cvtColor(ann,cv2.COLOR_BGR2RGB); pil=Image.fromarray(rgb); pil.thumbnail((card_w-16,img_h),Image.Resampling.LANCZOS)
        px=x+(card_w-pil.width)//2; py=y+header+(img_h-pil.height)//2; canvas.paste(pil,(px,py))
        d.text((x+14,y+header+img_h+6),f'Pose: {"yes" if pose_lms else "no"} · Detected hands: {len(raw_hands)}',font=load_font(13),fill='#536975')
    overlay_path=out/'hello_reference_mediapipe_overlays.png'; canvas.save(overlay_path)

    # Enlarged hand-only numbered crops for every selected frame with detected hands.
    crops=[]
    for t,idx,ann,orig,raw_hands,pose_lms in selected:
        if raw_hands:
            crops.append((t,idx,crop_around_hand(orig,raw_hands[0])))
    if crops:
        cols=3; card_w=480; img_h=420; header=44; gap=18; margin=28; rows=math.ceil(len(crops)/cols)
        detail=Image.new('RGB',(margin*2+cols*card_w+(cols-1)*gap,130+rows*(header+img_h+gap)),'#F5F8F9')
        dd=ImageDraw.Draw(detail)
        dd.text((margin,24),'HAND DETAIL · NUMBERED 21-POINT LANDMARKS',font=load_font(24,True),fill='#153246')
        dd.text((margin,64),'Numbers identify MediaPipe Hands landmark indices 0–20 on the actual detected hand crop.',font=load_font(14),fill='#536975')
        for i,(t,idx,crop) in enumerate(crops):
            r=i//cols;c=i%cols;x=margin+c*(card_w+gap);y=110+r*(header+img_h+gap)
            dd.rounded_rectangle((x,y,x+card_w,y+header+img_h),radius=12,fill='white',outline='#D9E2E6',width=2)
            dd.text((x+12,y+10),f'FRAME {idx} · {t:.2f}s',font=load_font(15,True),fill='#176B61')
            cp=cv2.cvtColor(crop,cv2.COLOR_BGR2RGB); pp=Image.fromarray(cp); pp.thumbnail((card_w-16,img_h),Image.Resampling.LANCZOS)
            px=x+(card_w-pp.width)//2; py=y+header+(img_h-pp.height)//2; detail.paste(pp,(px,py))
        detail_path=out/'hello_reference_hand_landmark_details.png'; detail.save(detail_path)
    else:
        detail_path=None

    json_path=out/'hello_reference_landmark_coordinates.json'
    json_path.write_text(json.dumps({
        'source_video':str(video),'fps':fps,'source_frames':total,'resolution':[width,height],
        'pose_mode':'current MediaPipe full-pose model; only indices 0–24 are drawn for INCLUDE-compatible layout; not historical upper_body_only detector parity',
        'hand_model':'MediaPipe Hands, 21 landmarks per detected hand','sampled_frames':frame_data,
    },indent=2),encoding='utf-8')
    files=[overlay_path,json_path]+([detail_path] if detail_path else [])
    zip_path=out/'hello_reference_landmark_export.zip'
    with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
        for p in files: z.write(p,p.name)
    print(f'Frames processed: {frame_idx}/{total}')
    print(f'Sampled frames with pose: {sum(bool(x["pose_detected"]) for x in frame_data)}/{len(frame_data)}')
    print(f'Sampled frames with at least one hand: {sum(x["hand_count"]>0 for x in frame_data)}/{len(frame_data)}')
    print(f'Created: {overlay_path}')
    if detail_path: print(f'Created: {detail_path}')
    print(f'Created: {json_path}')
    print(f'Created: {zip_path}')

if __name__=='__main__': main()
