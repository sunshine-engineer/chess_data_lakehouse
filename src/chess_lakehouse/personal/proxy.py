"""Same-origin authenticated streaming bridge; no corpus-sized request buffering."""
import http.client
import os
import re
from urllib.parse import urlsplit

from flask import request,Response,jsonify,stream_with_context
from .pgn import valid_game_id
from ..runtime import load as runtime, secret

ROUTES={'session':{'GET'},'signup':{'POST'},'login':{'POST'},'logout':{'POST'},'password':{'POST'},
    'libraries':{'GET'},'context':{'GET'},'imports':{'GET'},'imports/file':{'POST'},
    'imports/lichess':{'POST'},'imports/preview':{'POST'},'report':{'POST'},'games':{'POST'},
    'analysis':{'POST'},'positions':{'POST'},'review':{'POST'},'assistant':{'POST'},
    'studies':{'GET','POST'},'export':{'GET'},'board/preferences':{'GET','POST'},
    'board/position':{'POST'},'board/variations':{'POST'},'board/variations/remove':{'POST'},
    'board/analysis':{'POST'},'learning/examples':{'POST'},'learning/progress':{'GET'},'learning/attempt':{'POST'}}
ROUTES.update({f'repertoire/{name}':{'POST'} for name in ('start','status','control','list','exercise','reveal','attempt')})
ROUTES.update({'archives/catalog':{'GET'},'archives/jobs':{'GET','POST'},'archives/control':{'POST'},'archives/subscription':{'POST'}})


def register(app):
    """Register the same-origin authenticated streaming bridge to the private study service."""
    @app.before_request
    def protect_personal_writes():
        if not request.path.startswith('/api/personal/'):return None
        request.max_content_length=None if request.path=='/api/personal/imports/file' else 32768
        if request.method not in ('GET','HEAD'):
            if request.headers.get('X-Study-Action')!='1':return jsonify(error='Use the Personal Study controls.'),403
            origin=request.headers.get('Origin')
            if origin and origin.rstrip('/')!=request.host_url.rstrip('/'):
                return jsonify(error='Cross-origin account changes are not allowed.'),403

    @app.route('/api/personal/<path:route>',methods=['GET','POST'])
    def personal(route):
        methods=ROUTES.get(route,set())
        if route.startswith('games/') and valid_game_id(route[6:]):methods={'GET'}
        if re.fullmatch(r'libraries/[a-fA-F0-9-]{36}/versions',route):methods={'GET'}
        if re.fullmatch(r'libraries/[a-fA-F0-9-]{36}/sync',route):methods={'POST'}
        if re.fullmatch(r'imports/[a-fA-F0-9-]{36}',route):methods={'GET'}
        if re.fullmatch(r'imports/[a-fA-F0-9-]{36}/(?:cancel|retry)',route):methods={'POST'}
        if re.fullmatch(r'board/analysis/[a-fA-F0-9-]{36}',route):methods={'GET'}
        if request.method not in methods:return jsonify(error='Unknown study operation'),404
        token=secret('STUDY_API_TOKEN','')
        if not token:return jsonify(error='Start the Personal Study service.'),503
        settings=runtime();base=urlsplit(settings.server.study_url)
        if base.scheme not in ('http','https'):return jsonify(error='Invalid study service configuration'),503
        connection=(http.client.HTTPSConnection if base.scheme=='https' else http.client.HTTPConnection)(base.hostname,base.port,timeout=settings.server.proxy_timeout_seconds)
        headers={'Authorization':'Bearer '+token}
        for key in ('Cookie','X-CSRF-Token','Content-Type'):
            if request.headers.get(key):headers[key]=request.headers[key]
        body=None
        if request.method=='POST':
            body=request.stream
            if request.content_length is not None:headers['Content-Length']=str(request.content_length)
        query=('?'+request.query_string.decode('ascii')) if request.query_string else ''
        try:
            connection.request(request.method,(base.path.rstrip('/') or '')+'/'+route+query,body=body,headers=headers,
                encode_chunked=body is not None and request.content_length is None)
            upstream=connection.getresponse()
        except (OSError,http.client.HTTPException):
            connection.close();return jsonify(error='The Personal Study service is unavailable. Check its Docker container.'),503
        forwarded={key:value for key,value in upstream.getheaders() if key.lower() in
            ('content-type','content-disposition','x-content-sha256','set-cookie','cache-control','content-encoding')}
        def chunks():
            try:
                while block:=upstream.read(65536):yield block
            finally:upstream.close();connection.close()
        return Response(stream_with_context(chunks()),status=upstream.status,headers=forwarded,direct_passthrough=True)
