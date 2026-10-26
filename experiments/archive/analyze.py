import csv, collections, statistics as st, sys
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
R="results/"; O=sys.argv[1] if len(sys.argv)>1 else "results/"
names={'5g':'5G-like','lossy':'Lossy wireless','sat':'Satellite-like'}
S=['5g','lossy','sat']; summ={}
for sc in S:
    d=collections.defaultdict(list)
    for r in csv.reader(open(R+f'eval_{sc}.csv')): d[r[2]].append(r)
    summ[sc]={}
    for m,v in d.items():
        f=lambda i:[float(x[i]) for x in v]
        summ[sc][m]=dict(n=len(v),time=f(3),retx=f(6),rtt=f(9),gp=f(11),to=f(7),tx=f(5))
print("scenario,mode,n,time_ms_mean,time_sd,retx_mean,timeouts_mean,avg_rtt_ms,goodput_pps")
for sc in S:
    for m in ('aimd','rl'):
        s=summ[sc][m]
        print(f"{sc},{m},{s['n']},{st.mean(s['time']):.0f},{st.pstdev(s['time']):.0f},{st.mean(s['retx']):.0f},{st.mean(s['to']):.1f},{st.mean(s['rtt']):.1f},{st.mean(s['gp']):.0f}")
C={'aimd':'#c0392b','rl':'#1f77b4'}; L={'aimd':'Rule-based AIMD','rl':'Q-learning (frozen)'}
fig,ax=plt.subplots(1,3,figsize=(11,3.6))
for k,(key,title,lab) in enumerate([('time','Completion time','seconds'),('retx','Retransmitted packets','packets'),('rtt','Mean RTT','ms')]):
    w=0.36
    for j,m in enumerate(('aimd','rl')):
        vals=[st.mean(summ[sc][m][key])/(1000 if key=='time' else 1) for sc in S]
        err=[st.pstdev(summ[sc][m][key])/(1000 if key=='time' else 1) for sc in S]
        ax[k].bar([i+(j-.5)*w for i in range(3)],vals,w,yerr=err,color=C[m],label=L[m],capsize=2)
    ax[k].set_xticks(range(3)); ax[k].set_xticklabels([names[s] for s in S],fontsize=8)
    ax[k].set_title(title,fontsize=10); ax[k].set_ylabel(lab,fontsize=8)
    ax[k].set_yscale('log' if key!='rtt' else 'linear')
ax[0].legend(fontsize=7); plt.tight_layout(); plt.savefig(O+'sm_fig1_comparison.png',dpi=170); plt.close()
# learning curves
fig,ax=plt.subplots(1,3,figsize=(11,3.4))
for k,sc in enumerate(S):
    rows=list(csv.reader(open(R+f'train_{sc}.csv')))
    ep=[int(r[0]) for r in rows]; t=[float(r[4])/1000 for r in rows]
    ax[k].plot(ep,t,'o-',color='#1f77b4',ms=3)
    ax[k].axhline(st.mean(summ[sc]['aimd']['time'])*500/1200/1000,color='#c0392b',ls='--',lw=1,label='AIMD (scaled to 500 pkts)')
    ax[k].set_title(names[sc],fontsize=10); ax[k].set_xlabel('training episode',fontsize=8); ax[k].set_ylabel('time for 500 pkts (s)',fontsize=8)
ax[0].legend(fontsize=7); plt.tight_layout(); plt.savefig(O+'sm_fig2_learning.png',dpi=170); plt.close()
# cwnd traces
fig,ax=plt.subplots(1,3,figsize=(11,3.4))
for k,sc in enumerate(S):
    for m in ('aimd','rl'):
        xs=[];ys=[]
        for r in csv.reader(open(R+f'trace_{sc}_{m}_1.csv')):
            if r[0]=='time_ms': continue
            xs.append(float(r[0])/1000); ys.append(float(r[2]))
        ax[k].plot(xs,ys,color=C[m],lw=.9,label=L[m])
    ax[k].set_title(names[sc],fontsize=10); ax[k].set_xlabel('time (s)',fontsize=8); ax[k].set_ylabel('cwnd (packets)',fontsize=8)
ax[0].legend(fontsize=7); plt.tight_layout(); plt.savefig(O+'sm_fig3_cwnd.png',dpi=170); plt.close()
