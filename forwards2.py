#!/usr/bin/python
import config
import time
import simplejson
import threading
import Queue
import random
import sys
import csv
import os
import pprint
from socket import error

import subprocess
import smtplib

import apiclient.errors
import unigoogle

google_domain = config.GOOGLE_DOMAIN

cwd = os.path.dirname(os.path.realpath(__file__)) + '/'
tempfile = os.path.join(cwd,'temp_forwards2')
forwards_filename = os.path.join(config.OUTPUT_DIR,'forwards_report.csv')
send_as_filename = os.path.join(config.OUTPUT_DIR,'send_as_report.csv')
reply_to_filename = os.path.join(config.OUTPUT_DIR,'reply_to_report.csv')

recipients = config.REPORT_RECIPIENTS
gpg_address = config.REPORT_GPG_ADDRESS
email_from = config.REPORT_FROM

num_workers = config.NUM_WORKERS
job_q = Queue.Queue()
status_q = Queue.Queue()


def main():
    log("Starting report.")
    print "Retrieving list of all users...please wait."
    users = get_users()
    print "\nTotal Users: " + str(len(users))

    # Set up workers
    for i in range(num_workers - 1):
        clear_worker_log(i)
        worker = threading.Thread(target=get_forwards, args=(i, job_q, status_q))
        worker.setDaemon(True)
        worker.start()

    work_count = 0
    for u in users:
        work_count += 1
        job_q.put((str(work_count), u))
        # if work_count == 500: break # For debugging

    total_users = work_count
    start_time = time.time()
    last_status = start_time
    success = 0
    errors = 0
    retry_count = 0
    retries = dict()
    users_dict = dict()
    error_text = ''
    while work_count > 0:
        try:
            (user, data, forwards, reply_tos, send_as) = status_q.get()
        except Queue.Empty:
            break
        if data['result'] == "ok":
            # Update status every second
            if time.time() - last_status > 1:
                status = ''
                elapsed_time = time.time() - start_time
                gs = int((total_users - work_count) / elapsed_time)
                status += "(" + str(work_count) + "/" + str(total_users) + ") (" + str(retry_count) + \
                    " retries) (" + str(gs) + " g/s) (Active Threads: " + \
                    str(threading.activeCount()) + ")"
                print "\r " + status,

            users_dict[user]=dict()
            users_dict[user]['forwards']=forwards
            users_dict[user]['reply_tos']=reply_tos
            users_dict[user]['send_as']=send_as

            success += 1
        else:
            errors += 1
            print "***************************************"
            print str(data['thread']) + " - " + user + " had ERROR."
            error_text += str(data['thread']) + " - " + user + " had ERROR.\n"
            print "***************************************"
            users_dict[user] = None
        if retries.get(data['retries']) is None:
            retries[data['retries']] = 0
        retries[data['retries']] += 1
        retry_count += data['retries']
        work_count -= 1

    stop = time.time() + 5
    while job_q.unfinished_tasks and time.time()<stop:
        time.sleep(1)

    if job_q.unfinished_tasks:
	    write_errors(str(job_q.unfinished_tasks)+" unfinished tasks.")

    job_q.join()

    write_forwards_files(users_dict)

    #write_groups(users_dict)
    if errors > 0:
        write_errors(error_text)

    elapsed_time = time.time() - start_time
    print "\rFinished in " + str(int(elapsed_time)) + " seconds.                                   "
    sys.stdout.flush()
    print "Successfully retreived " + str(success) + " groups."
    print "-----------------------------------------------"
    print "Errors: " + str(errors)
    for r in retries:
        if r == 0:
            continue
        print str(r) + " retries: " + str(retries[r])

    if config.EMAIL_ENCRYPTED_REPORT:
        encrypt_and_send_report(forwards_filename,"Forwarding addresses report")
        encrypt_and_send_report(reply_to_filename,"Reply To addresses report")
        encrypt_and_send_report(send_as_filename,"Send As addresses report")
        
    log("Finished report.")


def encrypt_and_send_report(filename, subject):

    def send_report(body):
       # subject = 'Forwarding addresses report'
        recipients_string = ",".join(recipients)
        to = recipients
        fullbody = "From: "+email_from+"\r\n" + "To: " + recipients_string + "\r\nSubject: "+subject+"\r\n"+body
        mailserver = smtplib.SMTP(config.MAILSERVER)
        mailserver.sendmail(email_from, to, fullbody)
        mailserver.quit()

    cmd = config.GPG_PATH+' --yes -e -a -r '+gpg_address+' '+tempfile
    subprocess.call(cmd, shell=True)

    with open(tempfile+".asc") as f:
        data=f.read()
        send_report(data)
    

def write_forwards_files(users_dict):

    if os.path.exists(config.OUTPUT_DIR) == False:
        os.mkdir(config.OUTPUT_DIR)

    with open(forwards_filename,'w') as f:
        for user in users_dict:
            if users_dict[user] is None:
                f.write('User '+user+':  '+'Forward To:ERROR\n')
                continue
            if len(users_dict[user]['forwards']) > 0:
                for forward in users_dict[user]['forwards']:
                    f.write('User '+user+':  '+'Forward To:'+forward+"\n")
            elif len(users_dict[user]['forwards']) == 0:
                f.write('User '+user+':  '+'Forward To:None'+"\n")

    with open(send_as_filename,'w') as f:
        for user in users_dict:
            if users_dict[user] is None:
                f.write('User '+user+':  '+'Send As:ERROR\n')
                continue
            if len(users_dict[user]['send_as']) > 0:
                for send_as in users_dict[user]['send_as']:
                    f.write('User '+user+':  '+'Send As:'+send_as+"\n")
            elif len(users_dict[user]['send_as']) == 0:
                f.write('User '+user+':  '+'Send As:None'+"\n")   

    with open(reply_to_filename,'w') as f:
        for user in users_dict:
            if users_dict[user] is None:
                f.write('User '+user+':  '+'Reply To:ERROR\n')
                continue
            if len(users_dict[user]['reply_tos']) > 0:
                for reply_to in users_dict[user]['reply_tos']:
                    f.write('User '+user+':  '+'Reply To:'+reply_to+"\n")
            elif len(users_dict[user]['reply_tos']) == 0:
                f.write('User '+user+':  '+'Reply To:None'+"\n")  


def write_errors(text):
    with open(cwd + 'get_forwards_errors.txt', 'a') as f:
        f.write(time.strftime("%c") + '\n-------------------------------\n')
        f.write(text)

def get_users():
    auth = unigoogle.Auth()
    auth.load_auth()

    users = list()
    service = auth.api_service('admin', 'directory_v1')

    nextPageToken = ''
    count = 0
    retries = 0
    while nextPageToken is not None:
        try:
            result = service.users().list(customer='my_customer', domain=google_domain,
                                          pageToken=nextPageToken).execute()
        except apiclient.errors.HttpError, e:
            if retry(e, retries):
                retries += 1
                continue
        count += len(result['users'])
        print '\r' + str(count),
        sys.stdout.flush()
        for one in result['users']:
            users.append(one['primaryEmail'])

        if result.get('nextPageToken') is None:
            nextPageToken = None
        else:
            nextPageToken = result['nextPageToken']

    return users

def get_forwards(worker_num, in_q, out_q):
    time.sleep(random.random())
    auth = unigoogle.ServiceAuth()

    while True:
        try:  # in one step, make sure queue is not empty and take cmd from queue
            (count, user_address) = in_q.get()
            worker_log(worker_num,"Checked out: "+user_address+"\n")
        except Queue.Empty:
            continue

        forwards = list()
        reply_tos = list()
        send_as = list()
        data = dict()
        data['result'] = 'ok'
        data['thread'] = worker_num
        nextPageToken = ''
        data['retries'] = 0
        # Auth as the user we need to check
        auth.load_auth(user_address)
        gmailservice = auth.api_service('gmail', 'v1')

        timerstart = time.time()
        try:
            worker_log(worker_num,"Trying Google API for: "+user_address+"\n")
            result = gmailservice.users().settings().forwardingAddresses().list(userId=user_address).execute()
            worker_log(worker_num,"Finished Google API for: "+user_address+"\n")

            if result.get('forwardingAddresses') is not None:
                for one in result['forwardingAddresses']:
                    forwards.append(one['forwardingEmail'])

            result = gmailservice.users().settings().sendAs().list(userId=user_address).execute()
            if result.get('sendAs') is not None:
                for one in result['sendAs']:
                    send_as.append(one['sendAsEmail'])
                    if one['replyToAddress'] != '':
                        reply_tos.append(one['replyToAddress'])
                        
            worker_log(worker_num,"Trying Google API for: "+user_address+"\n")
	
        except:
            e = sys.exc_info()[0]
            print e
            forwards.append("ERROR")
            worker_log(worker_num,"Exception for: "+user_address+" "+str(e)+"\n")


        worker_log(worker_num,"Checking in: "+user_address+"\n")
        out_q.put((user_address, data, forwards, reply_tos, send_as))
        worker_log(worker_num,"Checked in: "+user_address+"\n")

        in_q.task_done()
        worker_log(worker_num,"Task Done: "+user_address+"\n\n")

def ignore_error(e):
    # Find errors that are safe to ignore and don't need to be retried
    error = simplejson.loads(e.content)
    reason = error['error']['errors'][0]['reason']
    message = error['error']['errors'][0]['message']
    if reason == 'failedPrecondition' and message == 'Mail service not enabled':
        return True
    if reason == 'failedPrecondition':
        return True
    return False

def retry(e, retries):
    try:
        # Load Json body.
        error = simplejson.loads(e.content)
        reason = error['error']['errors'][0]['reason']

        if retries <= 2 and (reason == 'backendError' or reason == 'internalError'):
            print "DEBUG: Retrying because of 500 error."
            return True

        if retries == 5:
            return False
        if reason == 'userRateLimitExceeded' or reason == 'quotaExceeded':
            amount = (retries + 1) * 2
            time.sleep((retries + 1) * 2)
            return True
        # More error information can be retrieved with error.get('errors').
    except:
        # Could not load Json body.
        print "error in retry function"
        print str(e.resp.status) + " " + str(e.resp.reason)

    return False

def log(text):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(cwd,"log.txt"),'a') as f:
        f.write(timestamp+" -- FORWARDS: "+text+"\n")

def worker_log(worker_num, text):
    if (config.DEBUG_WORKERS): # this is for debugging, everything seems to work OK now
        if os.path.exists(os.path.join(cwd,"debug")) == False:
            os.mkdir(os.path.join(cwd,"debug"))
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(cwd,"debug","worker_"+str(worker_num)+".log"),'a') as f:
            f.write(str(timestamp)+": "+text)

def clear_worker_log(num):
    if os.path.exists(os.path.join(cwd,"debug")) == False:
        return

	logfile = os.path.join(cwd,"debug","worker_"+str(num)+".log")
	if os.path.isfile(logfile):
		os.unlink(logfile)

main()