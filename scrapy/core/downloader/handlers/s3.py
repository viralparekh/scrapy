import six
from six.moves.urllib.parse import unquote

from scrapy.exceptions import NotConfigured
from scrapy.utils.httpobj import urlparse_cached
from scrapy.utils.python import to_unicode
from .http import HTTPDownloadHandler


def get_s3_connection():
    try:
        from boto.s3.connection import S3Connection
    except ImportError:
        return None

    class _v19_S3Connection(S3Connection):
        """A dummy S3Connection wrapper that doesn't do any synchronous download"""
        def _mexe(self, method, bucket, key, headers, *args, **kwargs):
            return headers

    class _v20_S3Connection(S3Connection):
        """A dummy S3Connection wrapper that doesn't do any synchronous download"""
        def _mexe(self, http_request, *args, **kwargs):
            http_request.authorize(connection=self)
            return http_request.headers

    try:
        import boto.auth
    except ImportError:
        _S3Connection = _v19_S3Connection
    else:
        _S3Connection = _v20_S3Connection

    return _S3Connection


class S3DownloadHandler(object):

    def __init__(self, settings, aws_access_key_id=None, aws_secret_access_key=None, \
            httpdownloadhandler=HTTPDownloadHandler, **kw):

        if not aws_access_key_id:
            aws_access_key_id = settings['AWS_ACCESS_KEY_ID']
        if not aws_secret_access_key:
            aws_secret_access_key = settings['AWS_SECRET_ACCESS_KEY']

        # If no credentials could be found anywhere,
        # consider this an anonymous connection request by default;
        # unless 'anon' was set explicitly (True/False).
        anon = kw.get('anon')
        if anon is None and not aws_access_key_id and not aws_secret_access_key:
            kw['anon'] = True
        self.anon = kw.get('anon')

        self._signer = None
        try:
            import botocore.auth
            import botocore.credentials
        except ImportError:
            if six.PY3:
                raise NotConfigured("missing botocore library")
            _S3Connection = get_s3_connection()
            if _S3Connection is None:
                raise NotConfigured("missing botocore or boto library")
            try:
                self.conn = _S3Connection(
                    aws_access_key_id, aws_secret_access_key, **kw)
            except Exception as ex:
                raise NotConfigured(str(ex))
        else:
            SignerCls = botocore.auth.AUTH_TYPE_MAPS['s3']
            # TODO - anon
            self._signer = SignerCls(botocore.credentials.Credentials(
                aws_access_key_id, aws_secret_access_key))

        self._download_http = httpdownloadhandler(settings).download_request

    def download_request(self, request, spider):
        p = urlparse_cached(request)
        scheme = 'https' if request.meta.get('is_secure') else 'http'
        bucket = p.hostname
        path = p.path + '?' + p.query if p.query else p.path
        url = '%s://%s.s3.amazonaws.com%s' % (scheme, bucket, path)
        if self.anon:
            request = request.replace(url=url)
        elif self._signer is not None:
            import botocore.awsrequest
            from botocore.vendored.requests.structures import CaseInsensitiveDict
            print(url, request.headers)
            awsrequest = botocore.awsrequest.AWSRequest(
                method=request.method,
                url='%s://s3.amazonaws.com/%s%s' % (scheme, bucket, path),
                # TODO - move to a header method
                headers=CaseInsensitiveDict(
                    (to_unicode(key), to_unicode(b','.join(value)))
                    for key, value in request.headers.items()),
                data=request.body)
            self._signer.add_auth(awsrequest)
            request = request.replace(
                url=url, headers=awsrequest.headers.items())
        else:
            signed_headers = self.conn.make_request(
                    method=request.method,
                    bucket=bucket,
                    key=unquote(p.path),
                    query_args=unquote(p.query),
                    headers=request.headers,
                    data=request.body)
            request = request.replace(url=url, headers=signed_headers)
        return self._download_http(request, spider)
