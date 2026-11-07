"""One verified model publication per scenario, with labelled comparison evidence."""
import numpy as np
from ..ml.features import vector
from . import data


def evaluate(artifacts,report,row):
    run=report['run_id'];x=np.array([vector(row)],dtype=float)
    def probabilities(name):
        model=artifacts.model(run,name)
        return {str(label):float(value) for label,value in zip(model.classes_,model.predict_proba(x)[0])}
    result={'run_id':run,'input':row,'probabilities':probabilities('outcome'),
        'estimated_moves':float(artifacts.model(run,'length').predict(x)[0]),
        'source':{'month':report['month'],'engine':report['engine'],'created_at':report.get('created_at'),
            'test_first_date':report['split']['test']['first_date'],'test_last_date':report['split']['test']['last_date']},
        'scope':'Historical archive estimates from '+report['month']+'. These are not a chess-engine evaluation or a forecast of personal progress.',
        'comparable':data.comparable(row,report['engine'],report['month'],report['source_signature'])}
    if report.get('schema_version')==2:
        result['baselines']={'ratings_only':probabilities('ratings'),'class_prior':probabilities('prior'),
            'median_moves':float(artifacts.model(run,'length_baseline').predict(x)[0])}
    return result
