#!/usr/bin/env
# Copyright (c) 2012-2013 Mitch Garnaat http://garnaat.org/
# Copyright 2012-2014 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.
from tests import unittest
import datetime
import time

import mock
import six

import botocore.auth
import botocore.credentials
from botocore.compat import HTTPHeaders, urlsplit, parse_qs
from botocore.awsrequest import AWSRequest
from botocore.vendored.requests.models import Request


class TestHMACV1(unittest.TestCase):

    maxDiff = None

    def setUp(self):
        access_key = '44CF9590006BF252F707'
        secret_key = 'OtxrzxIsfpFjA7SwPzILwy8Bw21TLhquhboDYROV'
        self.credentials = botocore.credentials.Credentials(access_key,
                                                            secret_key)
        self.hmacv1 = botocore.auth.HmacV1Auth(self.credentials, None, None)
        self.date_mock = mock.patch('botocore.auth.formatdate')
        self.formatdate = self.date_mock.start()
        self.formatdate.return_value = 'Thu, 17 Nov 2005 18:49:58 GMT'

    def tearDown(self):
        self.date_mock.stop()

    def test_put(self):
        headers = {'Date': 'Thu, 17 Nov 2005 18:49:58 GMT',
                   'Content-Md5': 'c8fdb181845a4ca6b8fec737b3581d76',
                   'Content-Type': 'text/html',
                   'X-Amz-Meta-Author': 'foo@bar.com',
                   'X-Amz-Magic': 'abracadabra'}
        http_headers = HTTPHeaders.from_dict(headers)
        split = urlsplit('/quotes/nelson')
        cs = self.hmacv1.canonical_string('PUT', split, http_headers)
        expected_canonical = (
            "PUT\nc8fdb181845a4ca6b8fec737b3581d76\ntext/html\n"
            "Thu, 17 Nov 2005 18:49:58 GMT\nx-amz-magic:abracadabra\n"
            "x-amz-meta-author:foo@bar.com\n/quotes/nelson")
        expected_signature = 'jZNOcbfWmD/A/f3hSvVzXZjM2HU='
        self.assertEqual(cs, expected_canonical)
        sig = self.hmacv1.get_signature('PUT', split, http_headers)
        self.assertEqual(sig, expected_signature)

    def test_duplicate_headers(self):
        pairs = [('Date', 'Thu, 17 Nov 2005 18:49:58 GMT'),
                 ('Content-Md5', 'c8fdb181845a4ca6b8fec737b3581d76'),
                 ('Content-Type', 'text/html'),
                 ('X-Amz-Meta-Author', 'bar@baz.com'),
                 ('X-Amz-Meta-Author', 'foo@bar.com'),
                 ('X-Amz-Magic', 'abracadabra')]

        http_headers = HTTPHeaders.from_pairs(pairs)
        split = urlsplit('/quotes/nelson')
        sig = self.hmacv1.get_signature('PUT', split, http_headers)
        self.assertEqual(sig, 'kIdMxyiYB+F+83zYGR6sSb3ICcE=')

    def test_query_string(self):
        split = urlsplit('/quotes/nelson?uploads')
        pairs = [('Date', 'Thu, 17 Nov 2005 18:49:58 GMT')]
        sig = self.hmacv1.get_signature('PUT', split,
                                        HTTPHeaders.from_pairs(pairs))
        self.assertEqual(sig, 'P7pBz3Z4p3GxysRSJ/gR8nk7D4o=')

    def test_bucket_operations(self):
        # Check that the standard operations on buckets that are
        # specified as query strings end up in the canonical resource.
        operations = ('acl', 'cors', 'lifecycle', 'policy',
                      'notification', 'logging', 'tagging',
                      'requestPayment', 'versioning', 'website')
        for operation in operations:
            url = '/quotes?%s' % operation
            split = urlsplit(url)
            cr = self.hmacv1.canonical_resource(split)
            self.assertEqual(cr, '/quotes?%s' % operation)

    def test_sign_with_token(self):
        credentials = botocore.credentials.Credentials(
            access_key='foo', secret_key='bar', token='baz')
        auth = botocore.auth.HmacV1Auth(credentials)
        request = AWSRequest()
        request.headers['Date'] = 'Thu, 17 Nov 2005 18:49:58 GMT'
        request.headers['Content-Type'] = 'text/html'
        request.method = 'PUT'
        request.url = 'https://s3.amazonaws.com/bucket/key'
        auth.add_auth(request)
        self.assertIn('Authorization', request.headers)
        # We're not actually checking the signature here, we're
        # just making sure the auth header has the right format.
        self.assertTrue(request.headers['Authorization'].startswith('AWS '))

    def test_resign_with_token(self):
        credentials = botocore.credentials.Credentials(
            access_key='foo', secret_key='bar', token='baz')
        auth = botocore.auth.HmacV1Auth(credentials)
        request = AWSRequest()
        request.headers['Date'] = 'Thu, 17 Nov 2005 18:49:58 GMT'
        request.headers['Content-Type'] = 'text/html'
        request.method = 'PUT'
        request.url = 'https://s3.amazonaws.com/bucket/key'

        auth.add_auth(request)
        original_auth = request.headers['Authorization']
        # Resigning the request shouldn't change the authorization
        # header.  We are also ensuring that the date stays the same
        # because we're mocking out the formatdate() call.  There's
        # another unit test that verifies we use the latest time
        # when we sign the request.
        auth.add_auth(request)
        self.assertEqual(request.headers.get_all('Authorization'),
                         [original_auth])

    def test_resign_uses_most_recent_date(self):
        dates = [
            'Thu, 17 Nov 2005 18:49:58 GMT',
            'Thu, 17 Nov 2014 20:00:00 GMT',
        ]
        self.formatdate.side_effect = dates

        request = AWSRequest()
        request.headers['Content-Type'] = 'text/html'
        request.method = 'PUT'
        request.url = 'https://s3.amazonaws.com/bucket/key'

        self.hmacv1.add_auth(request)
        original_date = request.headers['Date']

        self.hmacv1.add_auth(request)
        modified_date = request.headers['Date']

        # Each time we sign a request, we make another call to formatdate()
        # so we should have a different date header each time.
        self.assertEqual(original_date, dates[0])
        self.assertEqual(modified_date, dates[1])


class TestSigV2(unittest.TestCase):

    maxDiff = None

    def setUp(self):
        access_key = 'foo'
        secret_key = 'bar'
        self.credentials = botocore.credentials.Credentials(access_key,
                                                            secret_key)
        self.signer = botocore.auth.SigV2Auth(self.credentials)
        self.time_patcher = mock.patch.object(
            botocore.auth.time, 'gmtime',
            mock.Mock(wraps=time.gmtime)
        )
        mocked_time = self.time_patcher.start()
        mocked_time.return_value = time.struct_time(
            [2014, 6, 20, 8, 40, 23, 4, 171, 0])

    def tearDown(self):
        self.time_patcher.stop()

    def test_put(self):
        request = mock.Mock()
        request.url = '/'
        request.method = 'POST'
        params = {'Foo': u'\u2713'}
        result = self.signer.calc_signature(request, params)
        self.assertEqual(
            result, ('Foo=%E2%9C%93',
                     u'VCtWuwaOL0yMffAT8W4y0AFW3W4KUykBqah9S40rB+Q='))

    def test_fields(self):
        request = Request()
        request.url = '/'
        request.method = 'POST'
        request.data = {'Foo': u'\u2713'}
        self.signer.add_auth(request)
        self.assertEqual(request.data['AWSAccessKeyId'], 'foo')
        self.assertEqual(request.data['Foo'], u'\u2713')
        self.assertEqual(request.data['Timestamp'], '2014-06-20T08:40:23Z')
        self.assertEqual(request.data['Signature'],
                         u'Tiecw+t51tok4dTT8B4bg47zxHEM/KcD55f2/x6K22o=')
        self.assertEqual(request.data['SignatureMethod'], 'HmacSHA256')
        self.assertEqual(request.data['SignatureVersion'], '2')


class TestSigV3(unittest.TestCase):

    maxDiff = None

    def setUp(self):
        self.access_key = 'access_key'
        self.secret_key = 'secret_key'
        self.credentials = botocore.credentials.Credentials(self.access_key,
                                                            self.secret_key)
        self.auth = botocore.auth.SigV3Auth(self.credentials)
        self.date_mock = mock.patch('botocore.auth.formatdate')
        self.formatdate = self.date_mock.start()
        self.formatdate.return_value = 'Thu, 17 Nov 2005 18:49:58 GMT'

    def tearDown(self):
        self.date_mock.stop()

    def test_signature_with_date_headers(self):
        request = AWSRequest()
        request.headers = {'Date': 'Thu, 17 Nov 2005 18:49:58 GMT'}
        request.url = 'https://route53.amazonaws.com'
        self.auth.add_auth(request)
        self.assertEqual(
            request.headers['X-Amzn-Authorization'],
            ('AWS3-HTTPS AWSAccessKeyId=access_key,Algorithm=HmacSHA256,'
             'Signature=M245fo86nVKI8rLpH4HgWs841sBTUKuwciiTpjMDgPs='))

    def test_resign_with_token(self):
        credentials = botocore.credentials.Credentials(
            access_key='foo', secret_key='bar', token='baz')
        auth = botocore.auth.SigV3Auth(credentials)
        request = AWSRequest()
        request.headers['Date'] = 'Thu, 17 Nov 2005 18:49:58 GMT'
        request.method = 'PUT'
        request.url = 'https://route53.amazonaws.com/'
        auth.add_auth(request)
        original_auth = request.headers['X-Amzn-Authorization']
        # Resigning the request shouldn't change the authorization
        # header.
        auth.add_auth(request)
        self.assertEqual(request.headers.get_all('X-Amzn-Authorization'),
                         [original_auth])


class TestS3SigV4Auth(unittest.TestCase):

    maxDiff = None

    def setUp(self):
        self.credentials = botocore.credentials.Credentials(
            access_key='foo', secret_key='bar', token='baz')
        self.auth = botocore.auth.S3SigV4Auth(
            self.credentials, 'ec2', 'eu-central-1')
        self.request = AWSRequest(data=six.BytesIO(b"foo bar baz"))
        self.request.method = 'PUT'
        self.request.url = 'https://s3.eu-central-1.amazonaws.com/'

    def test_resign_with_content_hash(self):
        self.auth.add_auth(self.request)
        original_auth = self.request.headers['Authorization']

        self.auth.add_auth(self.request)
        self.assertEqual(self.request.headers.get_all('Authorization'),
                         [original_auth])

    def test_signature_is_not_normalized(self):
        request = AWSRequest()
        request.url = 'https://s3.amazonaws.com/bucket/foo/./bar/../bar'
        request.method = 'GET'
        credentials = botocore.credentials.Credentials('access_key',
                                                       'secret_key')
        auth = botocore.auth.S3SigV4Auth(credentials, 's3', 'us-east-1')
        auth.add_auth(request)
        self.assertTrue(
            request.headers['Authorization'].startswith('AWS4-HMAC-SHA256'))


class TestSigV4Resign(unittest.TestCase):

    maxDiff = None

    def setUp(self):
        self.credentials = botocore.credentials.Credentials(
            access_key='foo', secret_key='bar', token='baz')
        self.auth = botocore.auth.SigV4Auth(self.credentials,
                                            'ec2', 'us-west-2')
        self.request = AWSRequest()
        self.request.method = 'PUT'
        self.request.url = 'https://ec2.amazonaws.com/'

    def test_resign_request_with_date(self):
        self.request.headers['Date'] = 'Thu, 17 Nov 2005 18:49:58 GMT'
        self.auth.add_auth(self.request)
        original_auth = self.request.headers['Authorization']

        self.auth.add_auth(self.request)
        self.assertEqual(self.request.headers.get_all('Authorization'),
                         [original_auth])

    def test_sigv4_without_date(self):
        self.auth.add_auth(self.request)
        original_auth = self.request.headers['Authorization']

        self.auth.add_auth(self.request)
        self.assertEqual(self.request.headers.get_all('Authorization'),
                         [original_auth])


class TestSigV4Presign(unittest.TestCase):

    maxDiff = None

    def setUp(self):
        self.access_key = 'access_key'
        self.secret_key = 'secret_key'
        self.credentials = botocore.credentials.Credentials(self.access_key,
                                                            self.secret_key)
        self.service_name = 'myservice'
        self.region_name = 'myregion'
        self.auth = botocore.auth.SigV4QueryAuth(
            self.credentials, self.service_name, self.region_name, expires=60)
        self.datetime_patcher = mock.patch.object(
            botocore.auth.datetime, 'datetime',
            mock.Mock(wraps=datetime.datetime)
        )
        mocked_datetime = self.datetime_patcher.start()
        mocked_datetime.utcnow.return_value = datetime.datetime(
            2014, 1, 1, 0, 0)

    def tearDown(self):
        self.datetime_patcher.stop()

    def get_parsed_query_string(self, request):
        query_string_dict = parse_qs(urlsplit(request.url).query)
        # Also, parse_qs sets each value in the dict to be a list, but
        # because we know that we won't have repeated keys, we simplify
        # the dict and convert it back to a single value.
        for key in query_string_dict:
            query_string_dict[key] = query_string_dict[key][0]
        return query_string_dict

    def test_presign_no_params(self):
        request = AWSRequest()
        request.url = 'https://ec2.us-east-1.amazonaws.com/'
        self.auth.add_auth(request)
        query_string = self.get_parsed_query_string(request)
        self.assertEqual(
            query_string,
            {'X-Amz-Algorithm': 'AWS4-HMAC-SHA256',
             'X-Amz-Credential': ('access_key/20140101/myregion/'
                                  'myservice/aws4_request'),
             'X-Amz-Date': '20140101T000000Z',
             'X-Amz-Expires': '60',
             'X-Amz-Signature': ('c70e0bcdb4cd3ee324f71c78195445b878'
                                 '8315af0800bbbdbbb6d05a616fb84c'),
             'X-Amz-SignedHeaders': 'host'})

    def test_operation_params_before_auth_params(self):
        # The spec is picky about this.
        request = AWSRequest()
        request.url = 'https://ec2.us-east-1.amazonaws.com/?Action=MyOperation'
        self.auth.add_auth(request)
        # Verify auth params come after the existing params.
        self.assertIn(
            '?Action=MyOperation&X-Amz', request.url)

    def test_operation_params_before_auth_params_in_body(self):
        request = AWSRequest()
        request.url = 'https://ec2.us-east-1.amazonaws.com/'
        request.data = {'Action': 'MyOperation'}
        self.auth.add_auth(request)
        # Same situation, the params from request.data come before the auth
        # params in the query string.
        self.assertIn(
            '?Action=MyOperation&X-Amz', request.url)

    def test_presign_with_spaces_in_param(self):
        request = AWSRequest()
        request.url = 'https://ec2.us-east-1.amazonaws.com/'
        request.data = {'Action': 'MyOperation', 'Description': 'With Spaces'}
        self.auth.add_auth(request)
        # Verify we encode spaces as '%20, and we don't use '+'.
        self.assertIn('Description=With%20Spaces', request.url)

    def test_s3_sigv4_presign(self):
        auth = botocore.auth.S3SigV4QueryAuth(
            self.credentials, self.service_name, self.region_name, expires=60)
        request = AWSRequest()
        request.url = (
            'https://s3.us-west-2.amazonaws.com/mybucket/keyname/.bar')
        auth.add_auth(request)
        query_string = self.get_parsed_query_string(request)
        # We use a different payload:
        self.assertEqual(auth.payload(request), 'UNSIGNED-PAYLOAD')
        # which will result in a different X-Amz-Signature:
        self.assertEqual(
            query_string,
            {'X-Amz-Algorithm': 'AWS4-HMAC-SHA256',
             'X-Amz-Credential': ('access_key/20140101/myregion/'
                                  'myservice/aws4_request'),
             'X-Amz-Date': '20140101T000000Z',
             'X-Amz-Expires': '60',
             'X-Amz-Signature': ('ac1b8b9e47e8685c5c963d75e35e8741d55251'
                                 'cd955239cc1efad4dc7201db66'),
             'X-Amz-SignedHeaders': 'host'})

    def test_presign_with_security_token(self):
        self.credentials.token = 'security-token'
        auth = botocore.auth.S3SigV4QueryAuth(
            self.credentials, self.service_name, self.region_name, expires=60)
        request = AWSRequest()
        request.url = 'https://ec2.us-east-1.amazonaws.com/'
        auth.add_auth(request)
        query_string = self.get_parsed_query_string(request)
        self.assertEqual(
            query_string['X-Amz-Security-Token'], 'security-token')
