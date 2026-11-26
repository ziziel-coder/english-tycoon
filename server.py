from flask import Flask,request,jsonify,send_from_directory
from bank import B
import time,random,secrets,threading,os,re,csv,io
app=Flask(__name__); rooms={}; lock=threading.RLock()
def token(): return secrets.token_urlsafe(24)
def norm(x): return re.sub(r'\s+',' ',re.sub(r'[.,!?]','',str(x).lower()).strip())
def clock(r):
 elapsed=r['elapsed']+(time.monotonic()-r['since'] if r['status']=='playing' else 0)
 if elapsed>=sum(r['dur']) and r['status']=='playing': r['status']='ended'
 k=0
 while k<2 and elapsed>=sum(r['dur'][:k+1]): k+=1
 return k,max(0,int(sum(r['dur'][:k+1])-elapsed+0.999))
def rank(r): return sorted([dict(name=t['name'],money=round(t['money'],2),members=[r['players'][p]['name'] for p in t['ids']],accuracy=round(100*sum(r['players'][p]['correct'] for p in t['ids'])/max(1,sum(len(r['players'][p]['answers']) for p in t['ids'])),1)) for t in r['teams']],key=lambda t:(-t['money'],-t['accuracy']))
@app.get('/')
def home(): return send_from_directory('.', 'index.html')
@app.post('/api')
def api():
 d=request.get_json(); action=d.get('action'); pin=str(d.get('pin','')); key=d.get('key','')
 with lock:
  if action=='create':
   pin=str(random.randint(100000,999999))
   while pin in rooms: pin=str(random.randint(100000,999999))
   key=token(); rooms[pin]=dict(host=key,status='lobby',players={},teams=[],dur=[600]*3,elapsed=0,since=0); return jsonify(pin=pin,key=key,role='host')
  r=rooms.get(pin)
  if not r:return jsonify(error='Room not found. Check your PIN.'),404
  k,left=clock(r)
  if action=='join':
   if r['status']!='lobby':return jsonify(error='Game already started. Rejoin using your original device.'),400
   name=str(d.get('name','')).strip()[:60]
   if not name:return jsonify(error='Enter your name.'),400
   key=token();r['players'][key]=dict(name=name,cls=str(d.get('cls',''))[:40],answers={},correct=0,money=0,team=None,orders=[random.sample(range(i*20,i*20+20),20) for i in range(3)],streak=[0]*3,seen=time.monotonic(),msg='');return jsonify(pin=pin,key=key,role='student')
  host=key==r['host']; p=r['players'].get(key)
  if not host and not p:return jsonify(error='Invalid session.'),403
  if host:
   if action=='start' and r['status']=='lobby':
    ids=list(r['players']);random.shuffle(ids)
    if len(ids)<2:return jsonify(error='At least two students are needed.'),400
    try:r['dur']=[max(10,min(3600,int(x)*60)) for x in d.get('minutes',[10]*3)];assert len(r['dur'])==3
    except: return jsonify(error='Enter three valid round times.'),400
    groups=[ids[i:i+2] for i in range(0,len(ids),2)]
    if len(groups[-1])==1:groups[-2]+=groups.pop()
    for i,g in enumerate(groups):
     r['teams'].append(dict(name=f'Team {i+1}',ids=g,money=0,combo=False));
     for x in g:r['players'][x]['team']=i
    r['status']='playing';r['since']=time.monotonic()
   elif action=='pause' and r['status']=='playing':r['elapsed']+=time.monotonic()-r['since'];r['status']='paused'
   elif action=='resume' and r['status']=='paused':r['since']=time.monotonic();r['status']='playing'
   elif action=='end':r['status']='ended'
   elif action=='next' and r['status'] in ('playing','paused'):
    r['elapsed']=sum(r['dur'][:k+1]);r['since']=time.monotonic();r['status']='ended' if k==2 else r['status']
  if p:
   p['seen']=time.monotonic()
   if action=='message' and d.get('message') in ['Keep going!','Great job!','We can win!','Nice teamwork!']:p['msg']=d['message']
   if action=='answer':
    if r['status']!='playing':return jsonify(error='Round is not active.'),400
    try:i=int(d['id']);assert i in p['orders'][k] and i not in p['answers']
    except:return jsonify(error='Question already answered or round changed.'),400
    q=B[i];a=d.get('answer'); skip=d.get('skip',False)
    ok=not skip and (norm(a) in [norm(x) for x in q['a']] if q['t']=='S' else isinstance(a,list) and sorted(a)==q['a'])
    base=int({'M':100,'T':50,'C':200,'S':250}[q['t']]*[1,1.15,1.3][k]+.5);mult=2 if k==1 and r['dur'][1]/2-60<left<=r['dur'][1]/2 else 3 if k==2 and i==p['orders'][k][0] else 1
    earned=base*mult if ok else 0;team=r['teams'][p['team']];p['money']+=earned;p['correct']+=int(ok);p['answers'][i]=dict(ok=ok,earned=earned);team['money']+=earned*2/len(team['ids']);p['streak'][k]=p['streak'][k]+1 if ok else 0
    if k==0 and not team['combo'] and all(r['players'][x]['streak'][0]>=3 for x in team['ids']):team['combo']=True;team['money']+=500
    return jsonify(ok=ok,earned=earned,explanation=q['e'],correct=q['a'] if q['t']=='S' else [q['o'][j] for j in q['a']])
  k,left=clock(r); out=dict(status=r['status'],round=k,left=left,ranking=rank(r),count=len(r['players']),pin=pin)
  if host:out['students']=[dict(name=x['name'],cls=x['cls'],answered=len(x['answers']),correct=x['correct'],money=x['money'],online=time.monotonic()-x['seen']<15) for x in r['players'].values()]
  elif p['team'] is not None:
   team=r['teams'][p['team']];out.update(team=team['name'],money=round(team['money'],2),personal=p['money'],partners=[dict(name=r['players'][x]['name'],money=r['players'][x]['money'],message=r['players'][x]['msg'],online=time.monotonic()-r['players'][x]['seen']<15) for x in team['ids'] if x!=key],answered=sum(i in p['answers'] for i in p['orders'][k]))
   pending=[i for i in p['orders'][k] if i not in p['answers']]
   if pending and r['status']=='playing':
    i=pending[0];q=B[i];out['question']={**{x:q[x] for x in ['t','q','o']},'id':i};out['question']['reward']=int({'M':100,'T':50,'C':200,'S':250}[q['t']]*[1,1.15,1.3][k]+.5)*(2 if k==1 and r['dur'][1]/2-60<left<=r['dur'][1]/2 else 3 if k==2 and i==p['orders'][k][0] else 1)
   elif r['status']=='playing':out['practice']=[dict(id=i,q=B[i]['q'],correct=B[i]['a'] if B[i]['t']=='S' else [B[i]['o'][j] for j in B[i]['a']],explanation=B[i]['e']) for i in p['orders'][k] if not p['answers'][i]['ok']]
  return jsonify(out)
if __name__=='__main__':app.run(host='0.0.0.0',port=int(os.environ.get('PORT',8000)),threaded=True)
