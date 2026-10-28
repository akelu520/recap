import json, datetime as dt
cfg=json.load(open("config.json")); raw=json.load(open("raw.json"))
R=cfg["rules"]; V,G,T=R["volMult"],R["rsGap"],R["trigger"]
IDX=raw["IDX"]; D=raw["D"]
ir={r[0]:r[3] for r in IDX}; labels=[r[0] for r in IDX]; today=labels[-1]
sess=lambda l:(dt.date.fromisoformat(l)+dt.timedelta(days=1))
def sc(row,lb):
    rs=row[5]-ir[lb]; return int(row[3]>=V)+int(bool(row[4]))+int(rs>=G), rs
themes=[]
for t in cfg["themes"]:
    codes=[(c,n) for c,n in t["stocks"] if c in D]
    rm={c:{r[0]:r for r in D[c]} for c,_ in codes}
    tl=[{"d":sess(lb).strftime("%m/%d"),"n":sum(1 for c,_ in codes if lb in rm[c] and sc(rm[c][lb],lb)[0]==3)} for lb in labels]
    st=[]
    for c,n in codes:
        r=rm[c].get(today)
        if not r: continue
        s,rs=sc(r,today)
        st.append({"name":n,"code":c.split(".")[0],"px":r[1],"chg":r[2],"vol":r[3],"ma":bool(r[4]),"rs":round(rs,1),"score":s})
    if not st: continue
    st.sort(key=lambda x:(-x["score"],-x["rs"]))
    h=sum(1 for x in st if x["score"]==3)
    themes.append({"id":t["id"],"name":t["name"],"fired":h>=T,"hits":h,"total":len(st),
      "basketChg":round(sum(x["chg"] for x in st)/len(st),1),
      "avgScore":round(sum(x["score"] for x in st)/len(st),2),"stocks":st,"timeline":tl})
themes.sort(key=lambda t:(-int(t["fired"]),-t["hits"],-t["avgScore"],-t["basketChg"]))
d=sess(today).isoformat()
json.dump({"date":d,"updated":d+" 收盘","index":{"name":"深证成指","close":IDX[-1][1],"chg":IDX[-1][2]},"rules":R,"themes":themes},open("snapshot.json","w"),ensure_ascii=False)
print(d, [(t["name"],t["hits"],[x["n"] for x in t["timeline"]]) for t in themes])
