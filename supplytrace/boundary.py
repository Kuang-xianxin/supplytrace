"""ASGI admission covers request body, synchronous worker and response drain."""
import asyncio
import json
import time
from starlette.responses import JSONResponse

UPLOAD_LIMIT = 64*1024*1024


class LocalBoundary:
    def __init__(self, app, metrics, limit=16):
        self.app, self.metrics, self.limit = app,metrics,limit
        self.active = 0
        self.send_timeout = 30

    async def __call__(self, scope, receive, send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        headers = {k.decode('latin1').lower():v.decode('latin1') for k,v in scope.get('headers',[])}
        method, path = scope['method'],scope['path']
        started, size, status, sent = time.monotonic(),0,500,False
        async def bounded_send(message):
            nonlocal status,sent
            if message['type']=='http.response.start':
                if sent:
                    raise RuntimeError('response_already_started')
                sent = True;status=message['status']
                message['headers'] = list(message.get('headers',[]))+[
                    (b'x-content-type-options',b'nosniff'),
                    (b'content-security-policy',b"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")]
            # Every response, including admission rejection, has a bounded drain.
            await asyncio.wait_for(send(message),timeout=self.send_timeout)
        async def reject(code,status):
            await JSONResponse({'error':code},status_code=status,headers={'Retry-After':'3'} if status in (429,503) else {})(scope,receive,bounded_send)
        if method in ('POST','PUT','PATCH','DELETE'):
            origin = headers.get('origin')
            if (origin and origin not in ('http://127.0.0.1:8212','http://localhost:8212')) or headers.get('sec-fetch-site')=='cross-site':
                return await reject('origin_not_allowed',403)
        max_body = UPLOAD_LIMIT if path=='/api/workbench/imports' else 10000
        try:
            length = int(headers.get('content-length','0'))
            if length<0:
                raise ValueError
        except ValueError:
            return await reject('invalid_content_length',400)
        if length>max_body:
            return await reject('request_too_large',413)
        if self.active>=self.limit:
            self.metrics.record(503,0)
            return await reject('server_busy',503)
        # No await between check and increment: event-loop admission is atomic.
        self.active += 1
        async def bounded_receive():
            nonlocal size
            if time.monotonic()-started>120:
                raise BodyLimit('request_timeout',408)
            try:
                message = await asyncio.wait_for(receive(),timeout=15)
            except asyncio.TimeoutError:
                raise BodyLimit('request_timeout',408)
            size += len(message.get('body',b''))
            if size>max_body:
                raise BodyLimit('request_too_large',413)
            return message
        try:
            await self.app(scope,bounded_receive,bounded_send)
        except BodyLimit as exc:
            status=exc.status
            if not sent:
                await reject(exc.code,exc.status)
        finally:
            self.active -= 1
            self.metrics.record(status,time.monotonic()-started)


class BodyLimit(Exception):
    def __init__(self,code,status):
        self.code,self.status=code,status
