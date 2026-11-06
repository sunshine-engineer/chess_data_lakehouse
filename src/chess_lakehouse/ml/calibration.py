"""Explicit multiclass probability reliability diagnostics; no calibration claim by accuracy."""
import numpy as np


def reliability(model,x,y,bins=10):
    probabilities=model.predict_proba(x)
    labels=np.array(model.classes_)
    truth=(np.asarray(y)[:,None]==labels[None,:]).astype(float)
    curves={}
    errors={}
    for index,label in enumerate(labels):
        values=probabilities[:,index]
        bucket=np.minimum((values*bins).astype(int),bins-1)
        rows=[]
        error=0.
        for b in range(bins):
            mask=bucket==b
            n=int(mask.sum())
            if not n:
                continue
            predicted=float(values[mask].mean())
            observed=float(truth[mask,index].mean())
            error+=n/len(y)*abs(predicted-observed)
            rows.append(dict(lower=b/bins,upper=(b+1)/bins,count=n,
                mean_probability=predicted,observed_frequency=observed))
        curves[str(label)]=rows
        errors[str(label)]=error
    return dict(multiclass_brier=float(np.square(probabilities-truth).sum(axis=1).mean()),
        classwise_ece=errors,mean_classwise_ece=float(np.mean(list(errors.values()))),
        reliability_bins=curves,bins=bins,
        definition='Brier is the mean sum of squared errors across three classes (range 0–2). ECE is an empirical one-vs-rest bin diagnostic, not a guarantee.')
