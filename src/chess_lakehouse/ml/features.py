"""Shared pre-game feature contract used by training and inference."""
import math
import numpy as np

SPEEDS=('ultrabullet','bullet','blitz','rapid','classical','unknown')
FEATURES=('white_rating','black_rating','rating_difference','average_rating','initial_seconds','increment_seconds')+tuple('speed_'+s for s in SPEEDS)


def vector(row):
    """Construct the stable pre-game numerical feature vector used by training and inference."""
    w,b=row.get('white_rating'),row.get('black_rating')
    return [np.nan if w is None else w,np.nan if b is None else b,
        np.nan if w is None or b is None else w-b,
        np.nan if w is None or b is None else (w+b)/2,
        np.nan if row.get('initial_seconds') is None else row['initial_seconds'],
        np.nan if row.get('increment_seconds') is None else row['increment_seconds']]+[int(row.get('speed')==s) for s in SPEEDS]


def prediction_input(payload):
    """Validate a requested pre-game scenario without using post-game outcome information."""
    if not isinstance(payload,dict) or set(payload)-{'white_rating','black_rating','initial_seconds','increment_seconds'}:
        raise ValueError('provide ratings, initial_seconds and increment_seconds only')
    limits={'white_rating':(0,4000),'black_rating':(0,4000),'initial_seconds':(0,86400),'increment_seconds':(0,3600)}
    row={}
    for key,(low,high) in limits.items():
        value=payload.get(key)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
            raise ValueError(f'{key} must be a number between {low} and {high}')
        row[key]=value
    duration=row['initial_seconds']+40*row['increment_seconds']
    row['speed']='ultrabullet' if duration<30 else 'bullet' if duration<180 else 'blitz' if duration<480 else 'rapid' if duration<1500 else 'classical'
    return row
