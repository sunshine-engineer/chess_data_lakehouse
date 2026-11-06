"""Same-origin, route-allowlisted bridge to the isolated private study service."""
import os
import re
import urllib.error
import urllib.request

from flask import request,Response,jsonify

ROUTES={
    'context':{'GET'},'imports':{'POST'},'report':{'POST'},'positions':{'POST'},
    'cohorts':{'POST'},'review':{'POST'},'analysis':{'POST'},'assistant':{'POST'},
    'ml-validation':{'POST'},'export':{'GET'}
}


def register(app):
    @app.before_request
    def protect_personal_writes():
        if not request.path.startswith('/api/personal/'):
            return None
        if request.path=='/api/personal/imports':
            request.max_content_length=11*1024*1024
        else:
            request.max_content_length=16*1024
        if request.method not in ('GET','HEAD'):
            if request.headers.get('X-Study-Action')!='1':
                return jsonify(error='Use the same-origin personal-study controls.'),403
            origin=request.headers.get('Origin')
            if origin and origin.rstrip('/')!=request.host_url.rstrip('/'):
                return jsonify(error='Cross-origin personal changes are not allowed.'),403

    @app.route('/api/personal/<path:route>',methods=['GET','POST'])
    def personal(route):
        methods={'GET'} if re.fullmatch(r'games/[A-Za-z0-9]{8}',route) else ROUTES.get(route,set())
        if request.method not in methods:
            return jsonify(error='Unknown personal-study operation'),404
        base=os.environ.get('STUDY_SERVICE_URL','http://study-api:8081').rstrip('/')
        token=os.environ.get('STUDY_API_TOKEN','')
        if not token:
            return jsonify(error='Start the configured personal-study Docker services.'),503
        query=('?'+request.query_string.decode('ascii')) if request.query_string else ''
        headers={'Authorization':'Bearer '+token}
        body=None
        if request.method=='POST':
            body=request.get_data()
            headers['Content-Type']=request.content_type or 'application/json'
        upstream=urllib.request.Request(base+'/'+route+query,data=body,headers=headers,method=request.method)
        try:
            response=urllib.request.urlopen(upstream,timeout=30)
        except urllib.error.HTTPError as exc:
            response=exc
        except (urllib.error.URLError,TimeoutError):
            return jsonify(error='Personal study service unavailable. Start its Docker profile.'),503
        with response:
            payload=response.read(16*1024*1024+1)
            if len(payload)>16*1024*1024:
                return jsonify(error='Personal response exceeded the supported size.'),503
            output=Response(payload,status=response.status,content_type=response.headers.get('Content-Type','application/json'))
            for key in ('Content-Disposition','X-Content-SHA256'):
                if key in response.headers:
                    output.headers[key]=response.headers[key]
            return output
