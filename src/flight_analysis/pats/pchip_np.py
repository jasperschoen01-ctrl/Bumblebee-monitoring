import numpy as np

def _pchip_tangents(x, y):
    h = np.diff(x); d = np.diff(y)/h
    n = len(x); m = np.zeros(n)
    # interior (Fritsch–Carlson)
    for k in range(1, n-1):
        if d[k-1]==0 or d[k]==0 or np.sign(d[k-1])!=np.sign(d[k]):
            m[k]=0.0
        else:
            w1=2*h[k]+h[k-1]; w2=h[k]+2*h[k-1]
            m[k]=(w1+w2)/(w1/d[k-1]+w2/d[k])
    # endpoints (non-centered, shape-preserving — matches scipy PchipInterpolator)
    def end(h0,h1,d0,d1):
        m0=((2*h0+h1)*d0 - h0*d1)/(h0+h1)
        if np.sign(m0)!=np.sign(d0): m0=0.0
        elif np.sign(d0)!=np.sign(d1) and abs(m0)>3*abs(d0): m0=3*d0
        return m0
    if n==2:
        m[0]=m[1]=d[0]
    else:
        m[0]=end(h[0],h[1],d[0],d[1])
        m[-1]=end(h[-1],h[-2],d[-1],d[-2])
    return m

def pchip_fill(values):
    """Fill NaNs in a 1-D array via monotone cubic Hermite (PCHIP).
    Interior gaps -> PCHIP; leading/trailing NaNs -> nearest valid (flat),
    matching pandas interpolate(limit_direction='both')."""
    y=np.asarray(values,float).copy(); n=len(y)
    idx=np.arange(n); ok=~np.isnan(y)
    if ok.sum()<2: 
        if ok.sum()==1: y[:] = y[ok][0]
        return y
    xk=idx[ok]; yk=y[ok]; m=_pchip_tangents(xk.astype(float),yk)
    miss=np.where(~ok)[0]
    interior=miss[(miss>xk[0])&(miss<xk[-1])]
    for xx in interior:
        k=np.searchsorted(xk,xx)-1
        h=xk[k+1]-xk[k]; t=(xx-xk[k])/h
        h00=2*t**3-3*t**2+1; h10=t**3-2*t**2+t; h01=-2*t**3+3*t**2; h11=t**3-t**2
        y[xx]=yk[k]*h00+h*m[k]*h10+yk[k+1]*h01+h*m[k+1]*h11
    y[idx<xk[0]]=yk[0]; y[idx>xk[-1]]=yk[-1]   # flat edge extrapolation
    return y

if __name__=="__main__":
    # 1) exact on a line
    x=np.arange(10.0); y=3*x-2; yg=y.copy(); yg[4]=np.nan
    assert np.allclose(pchip_fill(yg),y), "line fail"
    # 2) monotone data: no overshoot (filled value stays within neighbor bounds)
    y=np.array([0,1,2,10,11,12,13.0]); yg=y.copy(); yg[3]=np.nan
    f=pchip_fill(yg); assert 2<=f[3]<=11, f"overshoot {f[3]}"
    # 3) edges
    yg=np.array([np.nan,np.nan,5,6,7,np.nan]); f=pchip_fill(yg)
    assert f[0]==5 and f[-1]==7, f"edge {f}"
    print("PCHIP unit tests PASS; line-fill val=", pchip_fill(np.array([0,1,np.nan,3,4.0]))[2])
