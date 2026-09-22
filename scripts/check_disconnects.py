"""Read-only, filtered gateway diagnostics. Never prints log text, keys or messages."""
from collections import Counter
from datetime import datetime, timezone
import json, os, pwd, re, stat, subprocess
from pathlib import Path

LIMIT=4_000_000
RULES=(
 ('heartbeat_blocked', r'heartbeat blocked for more than'),
 ('heartbeat_ack_timeout', r'(?:stopped responding to the gateway|heartbeat.*(?:ACK|ack|acknowledg).*(?:timeout|timed out))'),
 ('discord_reconnect_retry', r'Attempting a reconnect in'),
 ('discord_session_resumed', r'Shard ID [^ ]+ has successfully RESUMED'),
 ('discord_new_session', r'Shard ID [^ ]+ has connected to Gateway'),
 ('discord_connection_closed', r'(?:WebSocket closed with|websocket was closed with|ConnectionClosed:).*?(?:code|Code)?[ :]+[0-9]{4}'),
 ('discord_server_reconnect', r'Received RECONNECT opcode'),
 ('discord_invalid_session', r'(?:session has been invalidated|Invalid Session)'),
 ('connection_reset', r'(?:ConnectionResetError:|Connection reset by peer)'),
)

def scan(text):
 counts=Counter(); events=[]
 for line in text.splitlines():
  for label,pattern in RULES:
   if re.search(pattern,line):
    counts[label]+=1
    stamp=re.search(r'\b20[0-9]{2}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}',line)
    event={'event':label,'time':stamp[0] if stamp else 'not_on_this_line'}
    if label=='discord_connection_closed':
     codes=re.findall(r'\b(?:100[0-9]|101[0-5]|400[0-9]|401[0-4])\b',line)
     if codes:event['close_code']=int(codes[-1])
    events.append(event)
    break
 return {'counts':dict(counts),'last_events':events[-30:]}

def owned(path):
 info=path.lstat()
 if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():raise ValueError()
 with path.open('rb') as stream:
  stream.seek(max(0,info.st_size-LIMIT))
  return stream.read(LIMIT).decode('utf-8','replace'),info.st_size>LIMIT

def main():
 if pwd.getpwuid(os.getuid()).pw_name!='hermes':
  print('Run as hermes; no changes made.');return 2
 env=os.environ.copy();env['XDG_RUNTIME_DIR']=f'/run/user/{os.getuid()}'
 env['DBUS_SESSION_BUS_ADDRESS']='unix:path='+env['XDG_RUNTIME_DIR']+'/bus'
 result={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'service':{},'logs':[]}
 fields=('ActiveState','SubState','MainPID','NRestarts','ExecMainStartTimestamp')
 try:
  out=subprocess.run(['systemctl','--user','show','hermes-gateway.service',*[f'--property={f}' for f in fields]],env=env,capture_output=True,text=True,timeout=15)
  for line in out.stdout.splitlines():
   key,_,value=line.partition('=')
   if key in fields and re.fullmatch(r'[a-zA-Z0-9 :+.,/_-]{0,100}',value):result['service'][key]=value
 except Exception:result['service']['unavailable']=True
 root=Path.home()/'.hermes'
 for prefix,folder in (('default',root),('liberdus-mod',root/'profiles/liberdus-mod')):
  for name in ('gateway.log','gateway.error.log','errors.log','agent.log'):
   try:
    body,truncated=owned(folder/'logs'/name)
    result['logs'].append({'source':prefix+'/'+name,'tail_only':truncated,**scan(body)})
   except FileNotFoundError:pass
   except Exception:result['logs'].append({'source':prefix+'/'+name,'unavailable':True})
 try:
  out=subprocess.run(['journalctl','--user-unit=hermes-gateway.service','--since=36 hours ago','-n','4000','--no-pager','--output=json'],env=env,capture_output=True,text=True,timeout=20)
  lines=[]
  for raw in out.stdout.splitlines():
   entry=json.loads(raw);message=entry.get('MESSAGE','')
   if isinstance(message,str):
    timestamp=datetime.fromtimestamp(int(entry['__REALTIME_TIMESTAMP'])/1000000,timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    lines.extend(timestamp+' '+line for line in message.splitlines())
  result['journal']={'return_code':out.returncode,'bounded_records':len(out.stdout.splitlines()),**scan('\n'.join(lines))}
 except Exception:result['journal']={'unavailable':True}
 result['note']='Read-only bounded log scan. Sources may duplicate events; counters are not outage totals. Unmatched/no retained logs cannot rule out outages. No raw log text, tokens or message contents printed.'
 print(json.dumps(result,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
