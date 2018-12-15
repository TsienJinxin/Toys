#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'Personal Network Storage'

__author__ = 'mytpp'


import sys
import os
import shutil
import argparse
import yaml
import json
import asyncio
import hashlib
import sqlite3
import datetime
import time
import socket


import logging
logging.basicConfig(level=logging.INFO)

# handle Ctrl+C
import signal
signal.signal(signal.SIGINT, signal.SIG_DFL)


config = None # to be read from *.yaml
metaDB = None # meta database used only in tracker

def parse_config(file_name):
    # read config from *.yaml
    wd = os.path.split(os.path.realpath(__file__))[0]
    yaml_path = os.path.join(wd, file_name)
    global config 
    with open(yaml_path, 'r', encoding='utf-8') as f:
        content = f.read()
    config = yaml.load(content)
    this_ip = socket.gethostbyname(socket.gethostname())
    config['root'] = config['root'].rstrip('/')
    config['port'] = str(config['port'])
    config['ip'] = this_ip
    if 'tracker' in config:
        tracker_addr = config['tracker'].split(':')
        config['tracker_ip'] = tracker_addr[0]
        config['tracker_port'] = tracker_addr[1]

    logging.info('Load config successfully')
    # print(config)

def parse_header(header_str):
    header_str = header_str.strip()
    fields = header_str.split('\n')
    print(f"Received header:\n{fields!r}")
    header = {}
    for line in fields:
        # word_list = line.split(': ')
        # if word_list < 2
        #    continue
        key, value = line.split(': ')
        header[key] = value
    return header

# construct a request header
def make_header(cmd, length = 0):
    global config
    sha1 = hashlib.sha1()
    sha1.update(config['secret'].encode('utf-8'))
    sha1.update(cmd.encode('utf-8'))
    header  = 'V: ' + config['name'] + ' ' + config['port'] + '\n'
    header += 'A: ' + sha1.hexdigest() + '\n'
    header += 'C: ' + cmd + '\n'
    if length > 0:
        header += 'L: ' + str(length) + '\n'
    header += '\n'
    return header

# get error code
async def get_error(reader, writer):
    error = await reader.readuntil(b'\n\n') # 'error' is like b'E: 200 OK'
    err_msg = error.decode('utf-8').strip() 
    logging.info(err_msg)
    if err_msg.split(' ', 2)[1] != '200': # error occurs
        writer.close()
        return True

async def send_file(src, dst, writer, loop):
    logging.info('Start sending file...')
    size = os.path.getsize(src)
    with open(src, 'rb') as f:
        tr = writer.transport
        # 'size' and 'dst' is the reason why we need this header
        header = make_header('cp ' + src + ' ' + dst, size)
        print(f'Send: {header!r}')
        writer.write(header.encode('utf-8'))
        await writer.drain()
        # send file
        await loop.sendfile(tr, f) 
        writer.write_eof()
        await writer.drain()
    logging.info('Finish sending file.')

def local_to_physical(path):
    global config
    path = path.strip()
    if path.find(':') == -1: # path is like '/rootdir/file'
        return '//' + config['ip'] + ':' + config['port'] + path
    return '//' + config['ip'] + ':' + config['port'] + '/' + path


################################################################################
#---------------------------------Daemon Side----------------------------------#

#----------------------------Database Initiliztion-----------------------------#
def root_to_physical(localpath):
    return localpath.split(config['root'])[1]

async def load_path(root, istracker=True, cursor=None):
    global config
    path_list = [root + subpath for subpath in os.listdir(root)]
    for path in path_list:
        if not 'tracker' in config: # this is tracker
            logging.info('loading ' + path)
            ctime_stamp = os.path.getctime(path)
            mtime_stamp = os.path.getmtime(path)
            ctime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ctime_stamp))
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime_stamp))
            cursor.execute('''
                insert into filesystem
                (physical_path, category, ctime, mtime, size, host_addr, host_name)
                values  (?, ?, ?, ?, ?, ?, ?)
                ''', 
                (root_to_physical(path) , os.path.isfile(path), 
                ctime, mtime, os.path.getsize(path), 
                config['ip'] + ':' + config['port'], config['name'])
            )
            metaDB.commit()
        else:  # this is agent
            reader, writer = await asyncio.open_connection(
                            config['tracker_ip'], config['tracker_port'])
            header = make_header(
                    'ln ' + local_to_physical(path.split(config['root'])[1]), 
                    os.path.getsize(path))
            print(f'Send: {header!r}')
            writer.write(header.encode('utf-8'))
            await writer.drain()
            if await get_error(reader, writer):
                return
            logging.info('Link ' + path + ' successfully')
            writer.close()

        # recursive load path
        if(os.path.isdir(path)):
            await load_path(path + '/', istracker=istracker, cursor=cursor)
        
async def update_db():
    global config, metaDB
    if not 'tracker' in config: # this is tracker
        cursor = metaDB.cursor()
        cursor.execute('delete from filesystem where host_addr = ?', 
                        (config['ip'] + ':' + config['port'],))
        # insert physical paths in this host
        await load_path(config['root'] + '/', istracker=True, cursor=cursor)
        cursor.close()
    else:
        await load_path(config['root'] + '/', istracker=False)
            

def init_db():
    global config, metaDB
    metaDB = sqlite3.connect('pns.sqlite3')
    cursor = metaDB.cursor()
    # Both directory and file paths don't end with '/',
    # except logical root path
    cursor.execute('''
    create table if not exists filesystem (
        logical_path  varcahr,     -- all logical path is link
        physical_path varcahr,     -- is null if logical_path is not linked
        category      int,         -- 0: path, 1: file, 2: link
        ctime         varchar,
        mtime         varchar,
        size          int,         -- zero for dir
        host_addr     varchar,     -- ip and port
        host_name     varchar
    )
    ''')
    
    # if the database is empty, 
    # this is the first time we start the tracker
    cursor.execute('select count(*) from filesystem')
    if cursor.fetchone()[0] == 0:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # insert logical root
        cursor.execute('''
            insert into filesystem
            (logical_path, category, ctime, mtime, size)
            values ('/', 2, ?, ?, 0)
            ''', (now, now)
        )
    metaDB.commit()
    cursor.close()


#------------------------------Callback & Utilities---------------------------#

# break physical_path into name/ip and relative path
# Judge whether location denote a name or ip by whether it contains '.'
# Judge whether relative path is in root by whether it starts with '/'
def parse_physical_path(physical_path):
    physical_path = physical_path.split('/', 3)
    location = physical_path[2]
    path = '/'
    if len(physical_path) > 3:
        path = physical_path[3]
        if path.find(':') == -1:  # if path is not like 'c:/dir/f'
            path = '/' + path
    return location, path

# src is like '//137.0.0.1/local/path'
# dst is a logical path, like '/dir/a/file' or None
async def echo_ln(src, dst, host_name, writer, size=0):
    global metaDB
    cursor = metaDB.cursor()
    addr, path = parse_physical_path(src)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if dst: 
        dst.rstrip('/')
        cursor.execute('select count(*) from filesystem where logical_path = ?', (dst,))
        # if we're linking to an existing logical path
        if cursor.fetchone()[0] == 0:
            cursor.execute('insert into filesystem values (?, ?, ?, ?, ?, ?, ?, ?)',
                            (dst, path, 2, now, now, 0, addr, host_name))
        else:
            cursor.execute('''
                update into filesystem 
                set physical_path = ?, mtime = ?, host_addr = ?, host_name = ?
                where logical_path = ?
                ''', (path, now, addr, host_name, dst)
            )
        cursor.execute('delete from filesystem where physical_path = ?', (path,))
    else:
        # Judge whether the path is dir by whether it ends with '/'
        is_file = not path.endswith('/')
        path = path.rstrip('/')
        cursor.execute('''
            insert into filesystem
            (physical_path, category, ctime, mtime, size, host_addr, host_name)
            values  (?, ?, ?, ?, ?, ?, ?)
            ''', (path, is_file, now, now, size, addr, host_name))
        logging.info('update host %s\'s path' % host_name)

    # debug
    for row in cursor.execute('select * from filesystem'):
        print(row)
    cursor.close()
    metaDB.commit()

    writer.write(b'E: 200 OK\n\n')
    await writer.drain()


# dst is a logical path, like '/dir/a/file', 
# or a physical path, like '//137.0.0.1/local/path'
#                      or  '//h2/local/path'
async def echo_ls(dst, writer):
    global metaDB
    cursor = metaDB.cursor()
    file_list = []
    
    dst.rstrip('/')
    if dst.startswith('//'): # physical path
        location, path = parse_physical_path(dst)
        if location.find('.') != -1: # if location denotes an 'ip:port'
            cursor.execute('''
                select * from filesystem 
                where host_addr like ? and physical_path like ?
                order by physical_path asc
            ''', (location, path + '%%'))
        else: # if location denotes an name
            cursor.execute('''
                select * from filesystem 
                where host_name = ? and physical_path like ?
                order by physical_path asc
            ''', (location, path + '%%'))
            
        results = cursor.fetchall()
        if len(results) == 0:
            writer.write(b'E: 404 Path Not Found\n\n')
            await writer.drain()
            return
        for record in results:
            item = {
                'name' : record[1],
                'type' : 'f' if record[2] else 'd',
                'ctime': record[3],
                'mtime': record[4],
                'size' : record[5],
                'host' : record[6]  # ip and port
            }
            file_list.append(item)
    else: # logical path
        cursor.execute('''select * from filesystem where logical_path like ?
                            order by logical_path asc''', 
                        (dst + '%%', ))
        results = cursor.fetchall()
        if len(results) == 0:
            writer.write(b'E: 404 Path Not Found\n\n')
            await writer.drain()
            return
        for record in results:
            item = {
                'name' : record[0],
                'type' : record[1], # the physical path for this logical path
                'ctime': record[3],
                'mtime': record[4],
                'size' : record[5],
                'host' : record[6]  # ip and port
            }
        file_list.append(item)
    cursor.close()
    metaDB.commit()
    data = b'E: 200 OK\n\n' + json.dumps(file_list).encode('utf-8')
    # logging.info(data)
    writer.write(data) # need to add 'L' field in header?
    writer.write_eof()
    await writer.drain()


# dst is logical path, like '/logical/path'
async def echo_md(dst, writer):
    global metaDB
    cursor = metaDB.cursor()

    dst.rstrip('/')
    cursor.execute('select count(*) from filesystem where logical_path = ?', 
                    (dst,))
    if cursor.fetchone()[0] == 0: 
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            insert into filesystem
            (logical_path, category, ctime, mtime, size)
            values (?, 2, ?, ?, 0)
            ''', (dst, now, now)
        )
        metaDB.commit()
        writer.write(b'E: 200 OK\n\n')
        await writer.drain()
    else: # error
        writer.write(b'E: 406 Path Already Exists\n\n')
        await writer.drain()
    cursor.close()


# dst is logical path, like '/logical/path'
async def echo_rm(dst, writer):
    global metaDB
    cursor = metaDB.cursor()

    dst.rstrip('/')
    cursor.execute('select physical_path from filesystem where logical_path = ?',
                    (dst,))
    if cursor.fetchone()[0] == None:
        cursor.execute('delete from filesystem where logical_path = ?', (dst,))
    else:
        cursor.execute('''
            update into filesystem
            set logical_path = null
            where logical_path = ?
        ''', (dst,))
    writer.write(b'E: 200 OK\n\n')
    await writer.drain()


# src and dst should both be physical path like '//127.0.0.1/path
# Either src or dst must be this host,
# which determine whether this host sends or receives a file
# support only single file transfer
async def echo_cp(src, dst, reader, writer, size = 0):
    src_addr, src_path = parse_physical_path(src)
    dst_addr, dst_path = parse_physical_path(dst)
    
    if src_addr.split(':')[0] == config['ip']: # this is sending side
        if src_path.find(':') == -1:  # the file may be in root directory
            src_path = config['root'] + src_path
        if os.path.isfile(src_path):  # also return false if path doesn't exist
            loop = asyncio.get_running_loop()
            # 'dst_path' is not important here, as we send file through 'tr'
            await send_file(src_path, dst_path, writer, loop)
            if await get_error(reader, writer):
                return
            print('Send file successfully!')
        else:
            writer.write(b'E: 404 File Not Found')
            await writer.drain()

    elif dst_addr.split(':')[0] == config['ip']: # this is receiving side
        # allow only copying to root directory
        dst_path = config['root'] + dst_path
        if not os.path.exists(dst_path):  # if we are not covering a existing file
             with open(dst_path, 'wb') as f:
                if size > 0:
                    while size >= 4096:
                        f.write(await reader.read(4096))
                        size -= 4096
                    f.write(await reader.read(size))
                    writer.write(b'E: 200 OK\n\n')
                    await writer.drain()
                else:
                    writer.write(b'E: 400 No Length Field\n\n')
                    await writer.drain()
        else: 
            writer.write(b'E: 406 File Already Exists\n\n')
            await writer.drain()


async def echo_mv(src, dst, writer):
    pass


async def echo_illegal_command(writer):
    logging.info('Reciive illegal command')
    writer.write(b'E: 400 Illegal Command\n\n')
    await writer.drain()

async def echo_request(reader, writer):
    data = await reader.readuntil(b'\n\n')
    header = parse_header(data.decode('utf-8'))

    # check required fields
    if not 'V' in header:
        writer.write(b'E: 400 No Version Field\n\n')
        await writer.drain()
        return
    if not 'A' in header:
        writer.write(b'E: 400 No Authorization Field\n\n')
        await writer.drain()
        return
    if not 'C' in header:
        writer.write(b'E: 400 No Command Field\n\n')
        await writer.drain()
        return

    # check authorization
    global config
    sha1 = hashlib.sha1()
    sha1.update(config['secret'].encode('utf-8'))
    sha1.update(header['C'].encode('utf-8'))
    if sha1.hexdigest() != header['A']: # wrong password
        writer.write(b'E: 401 Unauthorized\n\n')
        await writer.drain()
        return
    

    # handle authorized request
    host_name = header['V'].split(' ')[0]
    cmd = header['C'].split(' ')
    if cmd[0] == 'ln':
        if len(cmd) < 2:
            await echo_illegal_command(writer)
            return
        elif len(cmd) == 2: # if we are link just a physical path
            cmd.append(None)
        if 'L' in header:
            await echo_ln(cmd[1], cmd[2], host_name, writer, int(header['L']))
        else:
            await echo_ln(cmd[1], cmd[2], host_name, writer)
    elif cmd[0] == 'ls':
        if len(cmd) < 2:
            await echo_illegal_command(writer)
            return
        await echo_ls(cmd[1], writer)
    elif cmd[0] == 'md':
        if len(cmd) < 2:
            await echo_illegal_command(writer)
            return
        await echo_md(cmd[1], writer)
    elif cmd[0] == 'rm':
        if len(cmd) < 2:
            await echo_illegal_command(writer)
            return
        await echo_rm(cmd[1], writer)
    elif cmd[0] == 'cp':
        if len(cmd) < 3:
            await echo_illegal_command(writer)
            return
        if 'L' in header:
            await echo_cp(cmd[1], cmd[2], reader, writer, int(header['L']))
        else:
            await echo_cp(cmd[1], cmd[2], reader, writer)
    elif cmd[0] == 'mv':
        if len(cmd) < 3:
            await echo_illegal_command(writer)
            return
        await echo_mv(cmd[1], cmd[2], writer)
    else:
        await echo_illegal_command(writer)
        return

    logging.info('Finish Echoing client.')
    logging.info('Continue serving...')



async def start_daemon():
    global config
    # if we're starting tracker, initiate meta database
    if not 'tracker' in config: 
        init_db()
    await update_db()
    
    server = await asyncio.start_server(
        echo_request, config['ip'], config['port'])
    addr = server.sockets[0].getsockname()
    print(f'Serving on {addr}')
    async with server:
        await server.serve_forever()

    # if this is tracker, close database
    if not 'tracker' in config:
        # close database
        global metaDB
        metaDB.commit()
        metaDB.close()


##############################################################################
#------------------------------Shell Side------------------------------------#

# judge if the path is in this host
def path_in_this_host(path):
    # os.path.exists(path) ?
    if path[0] != '/': # path is something like 'd:/pns/root/file'
        return True
    if path[1] != '/': # path is a logical path
        return False
    global config
    location = path.split('/', 3)[2]
    if config['ip'] == location or config['name'] == location:
        return True
    return False

def extract_local_path_from(path):
    if path[0] != '/': # path is something like 'd:/pns/root/file'
        return path
    global config      # path is something like '//nameOrIp/pns/root/file'
    relative_path = path.split('/', 3)[3] # not check if path.split('/', 3) has index 3
    return config['root'] + '/' + relative_path


async def ln(src, dst):
    reader, writer = await asyncio.open_connection(
            config['tracker_ip'], config['tracker_port'])
    header = make_header('ln ' + local_to_physical(src) + ' ' + dst)
    print(f'Send: {header!r}')
    writer.write(header.encode('utf-8'))
    await writer.drain()
    
    if await get_error(reader, writer):
        return
    print('Link successfully')
    writer.close()


async def ls(dst):
    reader, writer = await asyncio.open_connection(
            config['tracker_ip'], config['tracker_port'])
    header = make_header('ls ' + dst)
    print(f'Send: {header!r}')
    writer.write(header.encode('utf-8'))
    await writer.drain()

    if await get_error(reader, writer):
        return
    
    # get json data
    data = await reader.read()
    response = json.loads(data)
    print(response)
    writer.close()


async def md(dst):
    reader, writer = await asyncio.open_connection(
            config['tracker_ip'], config['tracker_port'])
    header = make_header('md ' + dst)
    print(f'Send: {header!r}')
    writer.write(header.encode('utf-8'))
    await writer.drain()
    
    if await get_error(reader, writer):
        return
    print('Make directory successfully')
    writer.close()


async def rm(dst):
    reader, writer = await asyncio.open_connection(
            config['tracker_ip'], config['tracker_port'])
    header = make_header('md ' + dst)
    print(f'Send: {header!r}')
    writer.write(header.encode('utf-8'))
    await writer.drain()
    
    if await get_error(reader, writer):
        return
    print('Remove successfully')
    writer.close()


# At least one of 'src' and 'dst' should be in this host.
# The one which is in other host is something like '//hostname/path' or '/logical/path'
async def cp(src, dst):
    src_is_here = path_in_this_host(src)
    dst_is_here = path_in_this_host(dst)
    if not src_is_here and not dst_is_here:
        print("Either src or dst should be in this host!")
    elif src_is_here and dst_is_here: 
        # use local filesystem
        src_path = extract_local_path_from(src)
        if os.path.exists(src_path):
            print('src doesn\'t exist!')
            return
        dst_path = extract_local_path_from(dst)
        shutil.copyfile(src_path, dst_path)

    elif src_is_here: # this is sending side
        src_path = extract_local_path_from(src)
        if os.path.exists(src_path):
            print('src doesn\'t exist!')
            return
        
        # try to get dst ip and port
        reader, writer = await asyncio.open_connection(
            config['tracker_ip'], config['tracker_port'])
        header = make_header('ls ' + dst)
        print(f'Send: {header!r}')
        writer.write(header.encode('utf-8'))
        await writer.drain()
        if await get_error(reader, writer):
            return
        # get response
        data = await reader.read()
        response = json.loads(data)
        dst_sock = response[0]['host'].split(':')
        relative_path = response[0]['type']
        writer.close()

        # send file to dst
        reader, writer = await asyncio.open_connection(dst_sock[0], dst_sock[1])
        loop = asyncio.get_running_loop()
        dst_path = '//' + dst_sock[0] + '/' + relative_path
        await send_file(src_path, dst_path, writer, loop)
        if await get_error(reader, writer):
            return
        print('Send file successfully!')

    else: # dst_is_here
        dst_path = extract_local_path_from(dst)
        if os.path.exists(dst_path):
            print('dst doesn\'t exist!')
            return
        
        # try to get src ip and port
        reader, writer = await asyncio.open_connection(
            config['tracker_ip'], config['tracker_port'])
        header = make_header('ls ' + src)
        print(f'Send: {header!r}')
        writer.write(header.encode('utf-8'))
        await writer.drain()
        if await get_error(reader, writer):
            return
        # get response
        data = await reader.read()
        response = json.loads(data)
        src_sock = response[0]['host'].split(':')
        relative_path = response[0]['type']
        writer.close()

        # send a header to let src host start sending file
        reader, writer = await asyncio.open_connection(src_sock[0], src_sock[1])
        loop = asyncio.get_running_loop()
        src_path = '//' + src_sock[0] + '/' + relative_path
        header = make_header('cp ' + src_path + ' ' + dst_path)
        print(f'Send: {header!r}')
        writer.write(header.encode('utf-8'))
        await writer.drain()

        # receive file from src
        rec_header = await reader.readuntil(b'\n\n')
        header = parse_header(rec_header.decode('utf-8'))
        if 'E' in header and header['E'].split(' ', 1)[0] != '200':
            logging.info(header['E'])
            writer.close()
            return
        if not 'L' in header:
            logging.info('No length field detcted.')
            writer.close()
            return
        size = int(header['L'])
        if size < 0:
            logging.info('Illegal length field.')
            writer.close()
            return
        with open(dst_path, 'wb') as f:
            while size >= 4096:
                f.write(await reader.read(4096))
                size -= 4096
            f.write(await reader.read(size))
            writer.write(b'E: 200 OK\n\n')
            await writer.drain()


async def mv(src, dst):
    pass


def do_command(cmd, *args):
    if cmd == 'cp':
        asyncio.run(cp(*args))
    elif cmd == 'ln':
        asyncio.run(ln(*args))
    elif cmd == 'ls':
        asyncio.run(ls(*args))
    elif cmd == 'md':
        asyncio.run(md(*args))
    elif cmd == 'mv':
        asyncio.run(mv(*args))
    elif cmd == 'rm':
        asyncio.run(mv(*args))
    else:
        print('Unknown Command')


#-----------------------------------Command Parser------------------------------------#

def main():
    parser = argparse.ArgumentParser(description='Personal Network Storage')
    parser.add_argument('-m', '--mode', dest='mode',
                        help='mode can be shell or daemon')
    parser.add_argument('-c', '--config', dest='config',
                        help='the config file (*.yml)')
    parser.add_argument('cmd', nargs='*',
                        help='(Available in shell mode) the command to be executed')

    args = parser.parse_args()

    if args.config:
        parse_config(args.config)
    else:
        print('No config file detected!')

    if args.mode == 'shell':
        if not args.cmd:
            print('Please enter a command')
        else:
            do_command(*(args.cmd))
    elif args.mode == 'daemon':
        logging.info('Starting daemon...')
        # for Windows, use iocp
        # if sys.platform == 'win32':
        #     asyncio.set_event_loop(asyncio.ProactorEventLoop())
        asyncio.run(start_daemon()) 
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
