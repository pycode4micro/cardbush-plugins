"""Head coverage geometry. No source texture is used for the plaster material."""
from __future__ import annotations
import cv2
import numpy as np


def tile_boxes(width, height, grid=3):
    tw,th=max(1,round(width*1.2/grid)),max(1,round(height*1.2/grid))
    xs=np.linspace(0,width-tw,grid).round().astype(int)
    ys=np.linspace(0,height-th,grid).round().astype(int)
    return list(dict.fromkeys((int(x),int(y),int(x+tw),int(y+th)) for y in ys for x in xs))


def head_proposals(probability, threshold):
    """Use independent head segmentation to give the face detector a tight ROI."""
    h,w=probability.shape
    mask=clean_head_mask(probability,threshold)
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    boxes=[]
    for contour in sorted(contours,key=cv2.contourArea,reverse=True)[:8]:
        x,y,bw,bh=cv2.boundingRect(contour)
        side=max(bw,bh)*1.8
        cx,cy=x+bw/2,y+bh/2
        boxes.append((max(0,int(cx-side/2)),max(0,int(cy-side/2)),
                      min(w,int(cx+side/2)),min(h,int(cy+side/2))))
    return boxes


def hair_supported_probability(hair, skin, threshold):
    """A disconnected skin patch (e.g. a hand) is not a head observation."""
    hair=np.squeeze(hair);skin=np.squeeze(skin)
    probability=hair+skin
    count,labels,stats,_=cv2.connectedComponentsWithStats((probability>=threshold).astype(np.uint8),8)
    support=cv2.dilate((hair>=max(.15,threshold*.5)).astype(np.uint8),np.ones((3,3),np.uint8))
    totals=np.bincount(labels[support>0],minlength=count)
    keep=totals>=np.maximum(3,stats[:,cv2.CC_STAT_AREA]*.02)
    keep[0]=False
    return probability*keep[labels]


def person_head_probability(probability, boxes, threshold, head_fraction):
    count,labels,stats,centers=cv2.connectedComponentsWithStats((probability>=threshold).astype(np.uint8),8)
    keep=np.zeros(count,bool)
    for index in range(1,count):
        _,_,width,height,_=stats[index]
        cx,cy=centers[index]
        for x0,y0,x1,y1 in boxes:
            if (x0<=cx<x1 and y0<=cy<=y0+(y1-y0)*head_fraction
                    and width>=max(3,(x1-x0)*.12) and height>=max(3,(y1-y0)*.045)):
                keep[index]=True;break
    return probability*keep[labels]


def map_landmarks(landmarks, box, width, height):
    points=np.array(landmarks,dtype=np.float32,copy=True)
    x0,y0,x1,y1=box
    points[:,0]=(points[:,0]*(x1-x0)+x0)/width
    points[:,1]=(points[:,1]*(y1-y0)+y0)/height
    points[:,2]*=(x1-x0)/width
    return points


def clean_head_mask(probability, threshold, padding=0):
    mask=(probability>=threshold).astype(np.uint8)*255
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    result=np.zeros_like(mask)
    minimum=max(8,mask.size*.00002)
    for contour in contours:
        if cv2.contourArea(contour)<minimum:continue
        component=np.zeros_like(mask)
        cv2.drawContours(component,[contour],-1,255,cv2.FILLED)
        _,_,w,h=cv2.boundingRect(contour)
        radius=round(min(w,h)*padding)
        if radius:
            component=cv2.dilate(component,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(radius*2+1,radius*2+1)))
        result=cv2.bitwise_or(result,component)
    return result


def ellipse_mask(shape, box):
    h,w=shape[:2]
    x0,y0,x1,y1=box
    result=np.zeros((h,w),np.uint8)
    cx,cy=round((x0+x1)*w/2),round((y0+y1)*h/2)
    rx,ry=max(1,round((x1-x0)*w/2)),max(1,round((y1-y0)*h/2))
    cv2.ellipse(result,(cx,cy),(rx,ry),0,0,360,255,-1)
    return result


def manual_mask(shape, regions, seconds):
    result=np.zeros(shape[:2],np.uint8)
    for region in regions:
        if not region.start_seconds<=seconds<region.end_seconds:continue
        keys=region.keyframes
        box=np.array(keys[0].box)
        if seconds>=keys[-1].seconds:box=np.array(keys[-1].box)
        else:
            for first,second in zip(keys,keys[1:]):
                if first.seconds<=seconds<second.seconds:
                    fraction=(seconds-first.seconds)/(second.seconds-first.seconds)
                    box=np.array(first.box)*(1-fraction)+np.array(second.box)*fraction
                    break
        result=cv2.bitwise_or(result,ellipse_mask(shape,box))
    return result


def plaster_fill(frame, mask):
    """Opaque, smoothly shaded white material; feather outward, never blur source."""
    if not np.any(mask):return frame
    result=frame.copy()
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour)
        x0,y0=max(0,x-2),max(0,y-2)
        x1,y1=min(frame.shape[1],x+w+2),min(frame.shape[0],y+h+2)
        patch_mask=np.zeros((y1-y0,x1-x0),np.uint8)
        cv2.drawContours(patch_mask,[contour-np.array([[[x0,y0]]])],-1,255,cv2.FILLED)
        yy,xx=np.mgrid[y0:y1,x0:x1]
        nx=(xx-(x+w*.5))/max(w*.55,1)
        ny=(yy-(y+h*.5))/max(h*.55,1)
        nz=np.sqrt(np.clip(1-nx*nx-ny*ny,0,1))
        light=np.clip(.62+.3*nz-.09*nx-.09*ny,.45,1.)
        material=light[...,None]*np.array([240,248,252])
        alpha=np.maximum(patch_mask/255.,cv2.GaussianBlur(patch_mask,(3,3),.55)/255.)[...,None]
        patch=result[y0:y1,x0:x1].astype(np.float32)
        result[y0:y1,x0:x1]=np.clip(patch*(1-alpha)+material*alpha,0,255).astype(np.uint8)
    return result


class TemporalCoverage:
    """Short forward-flow bridge. Only fresh observations reset its expiry."""
    def __init__(self, hold_seconds):
        self.hold_ms=hold_seconds*1000
        self.previous=None
        self.mask=None
        self.last_observed_ms=None
        self.signature=None

    def apply(self, frame, observed_mask, timestamp_ms):
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        scale=min(1.,480/max(gray.shape))
        small=cv2.resize(gray,None,fx=scale,fy=scale) if scale<1 else gray
        signature=cv2.resize(gray,(64,48)).astype(np.float32)
        cut=self.signature is not None and np.mean(np.abs(signature-self.signature))>32
        result=observed_mask
        bridged=False
        if (not np.any(result) and not cut and self.hold_ms>0 and self.previous is not None
                and self.mask is not None and self.last_observed_ms is not None
                and 0<timestamp_ms-self.last_observed_ms<=self.hold_ms):
            roi=cv2.resize(self.mask,(small.shape[1],small.shape[0]),interpolation=cv2.INTER_NEAREST)
            points=cv2.goodFeaturesToTrack(self.previous,maxCorners=100,qualityLevel=.01,minDistance=4,mask=roi)
            if points is not None and len(points)>=6:
                moved,status,_=cv2.calcOpticalFlowPyrLK(self.previous,small,points,None)
                if moved is not None:
                    back,back_status,_=cv2.calcOpticalFlowPyrLK(small,self.previous,moved,None)
                    if back is not None:
                        valid=(status.ravel()==1)&(back_status.ravel()==1)&(np.linalg.norm(back-points,axis=2).ravel()<1.5)
                        if np.count_nonzero(valid)>=6:
                            matrix,inliers=cv2.estimateAffinePartial2D(points[valid],moved[valid],method=cv2.RANSAC,ransacReprojThreshold=2)
                            if matrix is not None and inliers is not None and np.mean(inliers)>=.65:
                                factor=np.linalg.norm(matrix[0,:2])
                                error=np.mean(np.abs(cv2.warpAffine(self.previous,matrix,(small.shape[1],small.shape[0])).astype(float)-small)[roi>0])
                                if .8<=factor<=1.25 and error<22 and np.linalg.norm(matrix[:,2])<max(small.shape)*.18:
                                    matrix[:,2]/=scale
                                    result=cv2.warpAffine(self.mask,matrix,(gray.shape[1],gray.shape[0]),flags=cv2.INTER_NEAREST)
                                    bridged=bool(np.any(result))
        if np.any(observed_mask):self.last_observed_ms=timestamp_ms
        elif cut:self.last_observed_ms=None
        self.mask=result.copy() if np.any(result) else None
        self.previous=small.copy();self.signature=signature
        return result,bridged,cut
