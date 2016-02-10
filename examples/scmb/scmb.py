#!/usr/bin/env python3

###
# (C) Copyright (2012-2015) Hewlett Packard Enterprise Development LP
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
###

import sys
if sys.version_info < (3, 4):
    raise Exception('Must use Python 3.4 or later')

from hpOneView import *
import pika
from pika.credentials import ExternalCredentials
from pika.exceptions import ConnectionClosed
import json
import ssl
import datetime
import time


# Connection retry count
reconnectCount = 0

class RetryableConnectionClosed(Exception):
    pass

def callback(channel, method, properties, body):
    # ACK receipt of message
    channel.basic_ack(method.delivery_tag)

    # Convert from json into a Python dictionary
    body = json.loads(body.decode("utf-8"))
    # Create a new variable name 'resource' to point to the
    # nested resource dictionary inside of the body
    resource = body['resource']
    print(resource['uri'])
    print("\n")
    # Test to make sure that there is an alertState key
    # in the resource dictionary, if there is continue
    if 'alertState' in list(resource.keys()):
        # Filter only "Active" alerts
        if (('Active' == resource['alertState']) and
                ('Critical' == resource['severity']) and
                ('Created' == body['changeType'])):
            # Print out the requested information
            print('')
            print('original OneView alert:')
            print('------------------------------------------')
            print('changeType: %s' % (body['changeType']))
            print('data: %s' % (body['data']))
            print('eTag: %s' % (body['eTag']))
            print('newState: %s' % (body['newState']))
            print('resourceUri: %s' % (body['resourceUri']))
            print('resource:')
            print('------------------------------------------')
            print('    alertState: %s' % (resource['alertState']))
            print('    alertTypeID: %s' % (resource['alertTypeID']))
            print('    description: %s' % (resource['description']))
            print('    changeLog: %s' % (resource['changeLog']))
            print('    severity: %s' % (resource['severity']))
            print('    resourceName: %s'
                  % (resource['associatedResource']['resourceName']))
            print('    resourceCategory: %s'
                  % (resource['associatedResource']['resourceCategory']))
            print('    uri: %s' % (resource['uri']))
            # The timestamp from the appliance is in ISO 8601 format, convert
            # it to a Python datetime format instead
            atime = (datetime.datetime.strptime(body['timestamp'],
                                                '%Y-%m-%dT%H:%M:%S.%fZ'))
            # Print the timestamp is a simple format (still in UTC)
            print('timestamp: %s' % (atime.strftime('%Y-%m-%d %H:%M:%S')))
            print('resourceUri: %s' % (body['resourceUri']))
            print('')

    # Cancel this callback
    if body == 'quit':
        channel.basic_cancel(method.consumer_tag)

def recv(host, route):
    global reconnectCount
    # Create and bind to queue
    EXCHANGE_NAME = 'scmb'
    # Setup our ssl options
    ssl_options = ({'ca_certs': 'caroot.pem',
                    'certfile': 'client.pem',
                    'keyfile': 'key.pem',
                    'cert_reqs': ssl.CERT_REQUIRED,
                    'ssl_version' : ssl.PROTOCOL_TLSv1_1,
                    'server_side': False})

    # Connect to RabbitMQ
    try:
        conn = pika.BlockingConnection(pika.ConnectionParameters(
                   host, 5671, ssl=True, ssl_options=ssl_options, credentials=ExternalCredentials(), heartbeat_interval=10))
        # Reset reconnectCount after reconnect succeed
        if reconnectCount > 0:
            print('Reconnect succeeded!')
            print("\n")
            reconnectCount = 0
    except ConnectionClosed as ex:
        # If connection failed at the beginning no retry
        if reconnectCount == 0:
            raise ex
        else:
            raise RetryableConnectionClosed(ex)

    ch = conn.channel()
    qname = ch.queue_declare(auto_delete=True).method.queue
    ch.queue_bind(queue=qname, exchange=EXCHANGE_NAME, routing_key=route)
    ch.basic_consume(callback, queue=qname)

    # Start listening for messages
    try:
        ch.start_consuming() 
    except ConnectionClosed as ex:
        print('Connection closed! Try to reconnect. If connection is closed due to HA failover reconnect should succeed very soon.')
        raise RetryableConnectionClosed(ex)
    
    ch.close()
    conn.close()

def recvWithRetry(host, route):
    # When failover happens reconnect to the server.
    # Exit after maxReconnectCount retries
    maxReconnectCount = 180
    global reconnectCount
    while reconnectCount < maxReconnectCount:
        try:
            recv(host, route)
        except RetryableConnectionClosed:
            print('.', end="", flush=True)
            time.sleep(1)
            reconnectCount = reconnectCount+1
    print('Can not make connection after %d retries' % reconnectCount)
            
def login(con, credential):
    # Login with givin credentials
    try:
        con.login(credential)
    except:
        print('Login failed')


def acceptEULA(con):
    # See if we need to accept the EULA before we try to log in
    con.get_eula_status()
    try:
        if con.get_eula_status() is True:
            con.set_eula('no')
    except Exception as e:
        print('EXCEPTION:')
        print(e)


def getCertCa(sec):
    cert = sec.get_cert_ca()
    ca = open('caroot.pem', 'w+')
    ca.write(cert)
    ca.close()


def genRabbitCa(sec):
    sec.gen_rabbitmq_internal_signed_ca()


def getRabbitKp(sec):
    cert = sec.get_rabbitmq_kp()
    ca = open('client.pem', 'w+')
    ca.write(cert['base64SSLCertData'])
    ca.close()
    ca = open('key.pem', 'w+')
    ca.write(cert['base64SSLKeyData'])
    ca.close()


def main():
    parser = argparse.ArgumentParser(add_help=True, description='Usage')
    parser.add_argument('-a', '--appliance', dest='host', required=True,
                        help='HP OneView Appliance hostname or IP')
    parser.add_argument('-u', '--user', dest='user', required=False,
                        default='Administrator', help='HP OneView Username')
    parser.add_argument('-p', '--pass', dest='passwd', required=True,
                        help='HP OneView Password')
    parser.add_argument('-r', '--route', dest='route', required=False,
                        default='scmb.#', help='AMQP Routing Key')
    parser.add_argument('-g', '--gen', dest='gen', required=False,
                        action='store_true',
                        help='Generate the Rabbit MQ keypair and exit')
    parser.add_argument('-d', '--download', dest='down', required=False,
                        action='store_true',
                        help='Download the required keys and certs then exit')
    args = parser.parse_args()
    credential = {'userName': args.user, 'password': args.passwd}

    con = connection(args.host)
    sec = security(con)

    login(con, credential)
    acceptEULA(con)

    # Generate the RabbitMQ keypair (only needs to be done one time)
    if args.gen:
        genRabbitCa(sec)
        sys.exit()

    if args.down:
        getCertCa(sec)
        getRabbitKp(sec)
        sys.exit()

    recvWithRetry(args.host, args.route)

if __name__ == '__main__':
    import sys
    import argparse
    sys.exit(main())

# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
