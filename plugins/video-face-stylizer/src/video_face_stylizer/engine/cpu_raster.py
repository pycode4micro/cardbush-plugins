"""Compiled CPU triangle rasterization. No CUDA, OpenCL or OpenGL calls."""
import math
import numpy as np
from numba import njit

@njit(cache=True)
def rasterize(vertices, faces, xy, width, height, key, fill, half):
    depthbuffer=np.full((height,width),-np.inf,np.float32)
    rgba=np.zeros((height,width,4),np.uint8)
    for fi in range(len(faces)):
        ia,ib,ic=faces[fi]
        ax,ay=xy[ia]; bx,by=xy[ib]; cx,cy=xy[ic]
        xa=max(0,int(math.floor(min(ax,bx,cx))))
        xb=min(width,int(math.ceil(max(ax,bx,cx))))
        ya=max(0,int(math.floor(min(ay,by,cy))))
        yb=min(height,int(math.ceil(max(ay,by,cy))))
        denom=(by-cy)*(ax-cx)+(cx-bx)*(ay-cy)
        if abs(denom)<1e-7:continue
        inverse=1.0/denom
        for iy in range(ya,yb):
            y=iy+.5
            for ix in range(xa,xb):
                x=ix+.5
                a=((by-cy)*(x-cx)+(cx-bx)*(y-cy))*inverse
                b=((cy-ay)*(x-cx)+(ax-cx)*(y-cy))*inverse
                c=1-a-b
                if a < -1e-5 or b < -1e-5 or c < -1e-5:continue
                depth=a*vertices[ia,2]+b*vertices[ib,2]+c*vertices[ic,2]
                if depth<=depthbuffer[iy,ix]:continue
                depthbuffer[iy,ix]=depth
                nx=a*vertices[ia,3]+b*vertices[ib,3]+c*vertices[ic,3]
                ny=a*vertices[ia,4]+b*vertices[ib,4]+c*vertices[ic,4]
                nz=a*vertices[ia,5]+b*vertices[ib,5]+c*vertices[ic,5]
                invnorm=1.0/max(math.sqrt(nx*nx+ny*ny+nz*nz),1e-8)
                nx*=invnorm;ny*=invnorm;nz*=invnorm
                shade=a*vertices[ia,6]+b*vertices[ib,6]+c*vertices[ic,6]
                diffuse=max(nx*key[0]+ny*key[1]+nz*key[2],0.)
                fillamount=max(nx*fill[0]+ny*fill[1]+nz*fill[2],0.)
                spec=.035*max(nx*half[0]+ny*half[1]+nz*half[2],0.)**12
                intensity=min(max((.37+.57*diffuse+.11*fillamount)*shade+spec,0.),1.)
                rgba[iy,ix,0]=int(intensity*255+.5)
                rgba[iy,ix,1]=int(intensity*255*.993+.5)
                rgba[iy,ix,2]=int(intensity*255*.982+.5)
                rgba[iy,ix,3]=255
    return rgba
