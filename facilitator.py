#!/usr/bin/env python

import BaseHTTPServer
import SocketServer
import cgi
import getopt
import os
import re
import socket
import sys
import threading
import time
import urllib
import xml.sax.saxutils

DEFAULT_ADDRESS = "0.0.0.0"
DEFAULT_PORT = 9002
DEFAULT_LOG_FILENAME = "facilitator.log"

LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

class options(object):
    log_filename = DEFAULT_LOG_FILENAME
    log_file = sys.stdout
    daemonize = True

def usage(f = sys.stdout):
    print >> f, """\
Usage: %(progname)s <OPTIONS> [HOST] [PORT]
Flash bridge facilitator: Register client addresses with HTTP POST requests
and serve them out again with HTTP GET. Listen on HOST and PORT, by default
%(addr)s %(port)d.
  -d, --debug         don't daemonize, log to stdout.
  -h, --help          show this help.
  -l, --log FILENAME  write log to FILENAME (default \"%(log)s\").\
""" % {
    "progname": sys.argv[0],
    "addr": DEFAULT_ADDRESS,
    "port": DEFAULT_PORT,
    "log": DEFAULT_LOG_FILENAME,
}

log_lock = threading.Lock()
def log(msg):
    log_lock.acquire()
    try:
        print >> options.log_file, (u"%s %s" % (time.strftime(LOG_DATE_FORMAT), msg)).encode("UTF-8")
        options.log_file.flush()
    finally:
        log_lock.release()

def parse_addr_spec(spec, defhost = None, defport = None):
    host = None
    port = None
    m = None
    # IPv6 syntax.
    if not m:
        m = re.match(ur'^\[(.+)\]:(\d+)$', spec)
        if m:
            host, port = m.groups()
            af = socket.AF_INET6
    if not m:
        m = re.match(ur'^\[(.+)\]:?$', spec)
        if m:
            host, = m.groups()
            af = socket.AF_INET6
    # IPv4 syntax.
    if not m:
        m = re.match(ur'^(.+):(\d+)$', spec)
        if m:
            host, port = m.groups()
            af = socket.AF_INET
    if not m:
        m = re.match(ur'^:?(\d+)$', spec)
        if m:
            port, = m.groups()
            af = 0
    if not m:
        host = spec
        af = 0
    host = host or defhost
    port = port or defport
    if not (host and port):
        raise ValueError("Bad address specification \"%s\"" % spec)
    return af, host, int(port)

def format_addr(addr):
    host, port = addr
    if not host:
        return u":%d" % port
    # Numeric IPv6 address?
    try:
        addrs = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM, socket.IPPROTO_TCP, socket.AI_NUMERICHOST)
        af = addrs[0][0]
    except socket.gaierror, e:
        af = 0
    if af == socket.AF_INET6:
        return u"[%s]:%d" % (host, port)
    else:
        return u"%s:%d" % (host, port)

class TCPReg(object):
    def __init__(self, host, port):
        self.host = host
        self.port = port

    def __unicode__(self):
        return format_addr((self.host, self.port))

    def __str__(self):
        return unicode(self).encode("UTF-8")

    def __cmp__(self, other):
        if isinstance(other, TCPReg):
            return cmp((self.host, self.port), (other.host, other.port))
        else:
            return False

class RTMFPReg(object):
    def __init__(self, id):
        self.id = id

    def __unicode__(self):
        return u"%s" % self.id

    def __str__(self):
        return unicode(self).encode("UTF-8")

    def __cmp__(self, other):
        if isinstance(other, RTMFPReg):
            return cmp(self.id, other.id)
        else:
            return False

class Reg(object):
    @staticmethod
    def parse(spec, defhost = None, defport = None):
        try:
            af, host, port = parse_addr_spec(spec, defhost, defport)
        except ValueError:
            pass
        else:
            try:
                addrs = socket.getaddrinfo(host, port, af, socket.SOCK_STREAM, socket.IPPROTO_TCP, socket.AI_NUMERICHOST)
            except socket.gaierror, e:
                raise ValueError("Bad host or port: \"%s\" \"%s\": %s" % (host, port, str(e)))
            if not addrs:
                raise ValueError("Bad host or port: \"%s\" \"%s\"" % (host, port))

            host, port = socket.getnameinfo(addrs[0][4], socket.NI_NUMERICHOST | socket.NI_NUMERICSERV)
            return TCPReg(host, int(port))

        if re.match(ur'^[0-9A-Fa-f]{64}$', spec):
            return RTMFPReg(spec)

        raise ValueError("Bad spec format: %s" % repr(spec))

class RegSet(object):
    def __init__(self):
        self.set = []
        self.cv = threading.Condition()

    def add(self, reg):
        self.cv.acquire()
        try:
            if reg not in list(self.set):
                self.set.append(reg)
                self.cv.notify()
                return True
            else:
                return False
        finally:
            self.cv.release()

    def fetch(self):
        self.cv.acquire()
        try:
            if not self.set:
                return None
            return self.set.pop(0)
        finally:
            self.cv.release()

    def __len__(self):
        self.cv.acquire()
        try:
            return len(self.set)
        finally:
            self.cv.release()

class Handler(BaseHTTPServer.BaseHTTPRequestHandler):
    def do_GET(self):
        log(u"proxy %s connects" % format_addr(self.client_address))

        if self.path == "/crossdomain.xml":
            self.send_crossdomain()
            return
        
        client = ""
        reg = REGS.fetch()
        if reg:
            log(u"proxy %s gets %s (now %d)" % (format_addr(self.client_address), unicode(reg), len(REGS)))
            client = str(reg)
        else:
            log(u"proxy %s gets none" % format_addr(self.client_address))
            client = "Registration list empty"
        
        response = "client=%s" % urllib.quote(client)
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')    
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def do_POST(self):
        data = cgi.FieldStorage(fp = self.rfile, headers = self.headers, 
                                environ = {'REQUEST_METHOD' : 'POST',
                                           'CONTENT_TYPE' : self.headers['Content-Type']})

        client_specs = data["client"]
        if client_specs is None or client_specs.value is None:
            log(u"client %s missing \"client\" param" % format_addr(self.client_address))
            self.send_error(404)
            return
        val = client_specs.value

        try:
            reg = Reg.parse(val, self.client_address[0])
        except ValueError, e:
            log(u"client %s syntax error in %s: %s" % (format_addr(self.client_address), repr(val), repr(str(e))))
            self.send_error(404)
            return

        log(u"client %s regs %s -> %s" % (format_addr(self.client_address), val, unicode(reg)))
        if REGS.add(reg):
            log(u"client %s %s (now %d)" % (format_addr(self.client_address), unicode(reg), len(REGS)))
        else:
            log(u"client %s %s (already present, now %d)" % (format_addr(self.client_address), unicode(reg), len(REGS)))
        
        response = ""

        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(response)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format, *args):
        msg = format % args
        log(u"message from HTTP handler for %s: %s" % (format_addr(self.client_address), repr(msg)))
        
    def send_crossdomain(self):
        crossdomain = """\
<cross-domain-policy>
    <allow-access-from domain="*" to-ports="%s"/>
</cross-domain-policy>\r\n""" % xml.sax.saxutils.escape(str(address[1]))
        self.send_response(200)
        self.send_header('Content-Type', 'application/xml')
        self.send_header('Content-Length', str(len(crossdomain)))
        self.end_headers()
        self.wfile.write(crossdomain)  

REGS = RegSet()

opts, args = getopt.gnu_getopt(sys.argv[1:], "dhl:", ["debug", "help", "log="])
for o, a in opts:
    if o == "-d" or o == "--debug":
        options.daemonize = False
        options.log_filename = None
    elif o == "-h" or o == "--help":
        usage()
        sys.exit()
    elif o == "-l" or o == "--log":
        options.log_filename = a

if options.log_filename:
    options.log_file = open(options.log_filename, "a")
else:
    options.log_file = sys.stdout

if len(args) == 0:
    address = (DEFAULT_ADDRESS, DEFAULT_PORT)
elif len(args) == 1:
    # Either HOST or PORT may be omitted; figure out which one.
    if args[0].isdigit():
        address = (DEFAULT_ADDRESS, args[0])
    else:
        address = (args[0], DEFAULT_PORT)
elif len(args) == 2:
    address = (args[0], args[1])
else:
    usage(sys.stderr)
    sys.exit(1)

class Server(SocketServer.ThreadingMixIn, BaseHTTPServer.HTTPServer):
    pass

# Setup the server
server = Server(address, Handler)

log(u"start on %s" % format_addr(address))

if options.daemonize:
    log(u"daemonizing")
    if os.fork() != 0:
        sys.exit(0)

try:
    server.serve_forever()
except KeyboardInterrupt:
    sys.exit(0)
