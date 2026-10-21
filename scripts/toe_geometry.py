"""Pure geometry for a toe-joint pivot and a slightly extended EX tail."""
import math


def layout(ankle,pivot,tip,toe_ik=None,*,extension_ratio=.05):
    points=[tuple(float(v) for v in p) for p in (ankle,pivot,tip,toe_ik if toe_ik is not None else tip)]
    if any(len(p)!=3 or not all(math.isfinite(v) for v in p) for p in points):
        raise ValueError('Finite local foot coordinates required')
    if not math.isfinite(extension_ratio) or not 0<extension_ratio<=.2:
        raise ValueError('Use a small positive toe extension ratio')
    ankle,pivot,tip,toe_ik=points
    d=[tip[0]-ankle[0],tip[1]-ankle[1],0.]
    length=math.sqrt(sum(v*v for v in d))
    if length<1e-7:raise ValueError('Foot forward direction is degenerate')
    forward=[v/length for v in d]
    span=max(sum((p[i]-pivot[i])*forward[i] for i in range(3)) for p in (tip,toe_ik))
    if span< -length*1e-4:raise ValueError('Toe joint is in front of the control markers')
    margin=max(max(span,0.)*extension_ratio,length*.01)
    tail=[pivot[i]+forward[i]*(max(span,0.)+margin) for i in range(3)]
    return dict(head=list(pivot),tail=tail,forward=forward,extension=margin,
                extension_ratio=extension_ratio,marker_distance=span)
