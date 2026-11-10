from __future__ import annotations
import os, json, threading, time, uuid, shutil, zipfile, csv, io, sys, subprocess, sqlite3, sqlite3, hmac
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, send_file
from werkzeug.utils import secure_filename
import core

app=Flask(__name__, static_folder="static")

# Optional application-level Basic Auth. The Windows launchers require these
# values before starting a public Cloudflare tunnel.
WEB_USER=os.environ.get("FB_WEB_USER", "").strip()
WEB_PASSWORD=os.environ.get("FB_WEB_PASSWORD", "")

@app.before_request
def require_web_login():
    if not WEB_USER or not WEB_PASSWORD:
        return ("Website chua duoc cau hinh mat khau. Hay chay CHAY_WEB_WINDOWS.bat va cau hinh fb_web_config.bat.", 503)
    auth=request.authorization
    valid=(auth is not None and
           hmac.compare_digest(auth.username or "", WEB_USER) and
           hmac.compare_digest(auth.password or "", WEB_PASSWORD))
    if not valid:
        return ("Can dang nhap de su dung FB Auto Post.", 401,
                {"WWW-Authenticate": 'Basic realm="FB Auto Post", charset="UTF-8"'})
app.config["MAX_CONTENT_LENGTH"]=10*1024*1024*1024
store=core.Store()
HOME=core.HOME
UPLOADS=HOME/"web_uploads"
UPLOADS.mkdir(parents=True,exist_ok=True)
lock=threading.RLock()
state={"running":False,"stop":False,"logs":[],"current_ids":[],"worker":None}
ALLOWED={".jpg",".jpeg",".png",".webp"}

def log(msg):
    stamp=datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S")
    with lock:
        state["logs"].append({"time":stamp,"message":str(msg)})
        state["logs"]=state["logs"][-1500:]
    try: store.add_activity("Web",str(msg))
    except Exception: pass

def job_dict(j):
    return {"id":j.id,"group_url":j.group_url,"content":j.content,"images":j.images,
            "status":j.status,"label":core.LABELS.get(j.status,j.status),"note":j.note,
            "sent_intent_at":j.sent_intent_at,"created_at":j.created_at,
            "permalink":j.permalink,"evidence_path":j.evidence_path}

def start_worker(ids, delay=None, schedule_id=None, manual=False):
    with lock:
        if state["running"]: raise ValueError("Ứng dụng đang có lượt đăng. Hãy chờ hoặc dừng sau nhóm hiện tại.")
        state["running"]=True; state["stop"]=False
    def work():
        complete=True; sched_status="finished"; sched_error=""
        try:
            if manual:
                prev=store.get_app_state("manual_round_completed_at")
                if prev:
                    try: remaining=max(0,int(1800-(datetime.now().astimezone()-datetime.fromisoformat(prev).astimezone()).total_seconds()))
                    except Exception: remaining=0
                    if remaining:
                        log(f"Lượt trước vừa hoàn tất; chờ {remaining} giây để đủ 30 phút.")
                        if wait_stop(remaining):
                            complete=False; return
            with core.browser_batch_session() as page:
                for pos,jid in enumerate(ids):
                    if state["stop"]:
                        complete=False; log("Đã dừng theo yêu cầu; các nhóm còn lại chưa chạy."); break
                    job=store.get(jid)
                    if job.status!=core.QUEUED: continue
                    log(f"Nhóm {pos+1}/{len(ids)}: {job.group_url}")
                    try:
                        if not manual and not schedule_id:
                            prev=store.latest_sent_intent_for_payload(job.content,job.images,job.id)
                            if prev:
                                try: remaining=max(0,int(1800-(datetime.now().astimezone()-datetime.fromisoformat(prev).astimezone()).total_seconds()))
                                except Exception: remaining=0
                                if remaining:
                                    log(f"Cùng nội dung đã gửi gần đây; chờ {remaining} giây.")
                                    if wait_stop(remaining): complete=False; break
                        outcome=core.run_one(job,store,log,page=page)
                    except Exception as e:
                        latest=store.get(jid); detail=f"{type(e).__name__}: {str(e).splitlines()[0][:250]}"
                        if latest.sent_intent_at:
                            outcome=core.REVIEW; store.transition(jid,core.REVIEW,"Lỗi sau thao tác Đăng: "+detail+". Không tự thử lại.")
                        else:
                            outcome=core.FAILED; store.transition(jid,core.FAILED,"Bỏ qua do lỗi: "+detail)
                        log(f"Đã lưu lỗi nhóm {pos+1}; chuyển nhóm mới.")
                    log(f"Nhóm {pos+1}/{len(ids)}: {core.describe_outcome(outcome)}")
                    keep_going=(store.get_app_state("web_autocontinue") or "1")=="1"
                    if not core.can_advance_automatically(outcome,keep_going):
                        complete=False; log("Đã tạm dừng theo cài đặt tự động chuyển nhóm."); break
                    remaining=[k for k in ids[pos+1:] if store.get(k).status==core.QUEUED]
                    if remaining and outcome not in (core.FAILED,core.REVIEW,core.SKIPPED) and not manual:
                        d=max(0,int(delay if delay is not None else 15))
                        log(f"Nghỉ {d} giây trước nhóm tiếp theo.")
                        if wait_stop(d): complete=False; break
                    elif remaining and outcome in (core.FAILED,core.REVIEW,core.SKIPPED):
                        log("Nhóm lỗi/chưa rõ: bỏ qua ngay, chuyển nhóm kế tiếp không chờ.")
        except ImportError as e:
            complete=False; sched_status="error"; sched_error="Thiếu Playwright: "+str(e); log("Thiếu Playwright. Cài thư viện bằng nút Cài đặt.")
        except Exception as e:
            complete=False; sched_status="error"; sched_error=f"{type(e).__name__}: {str(e)[:250]}"; log("Lỗi chung: "+sched_error)
        finally:
            if manual and complete and not state["stop"]:
                try: store.set_app_state("manual_round_completed_at",core.now())
                except Exception: pass
            if schedule_id:
                if state["stop"] and sched_status=="finished": sched_status="stopped"
                store.update_schedule(schedule_id,sched_status,error=sched_error)
            with lock: state["running"]=False
            log("Đã kết thúc lượt đăng. Bài lỗi/chưa rõ được lưu, không tự đăng lại.")
    threading.Thread(target=work,daemon=True).start()

class _StopProxy:
    def is_set(self): return state["stop"]
    def wait(self, seconds): return wait_stop(seconds)
def wait_stop(seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if state["stop"]: return True
        time.sleep(min(1,end-time.monotonic()))
    return False

def schedule_loop():
    while True:
        try:
            if not state["running"]:
                now=datetime.now().astimezone()
                for item in store.list_schedules():
                    if item.get("status")!="scheduled": continue
                    try: due=core.parse_local_datetime(item["scheduled_at"])<=now
                    except Exception as e:
                        store.update_schedule(item["id"],"error",error=str(e)); continue
                    if due and store.claim_schedule(item["id"]):
                        log(f"Đến giờ lịch {item['title']}; đang khởi chạy.")
                        try:
                            groups=core.clean_groups(item["groups_text"])
                            images=core.validate_images(item["images"])
                            ids,_=store.add_jobs(item["content"],images,groups,repeat_key=f"schedule-{item['id']}")
                            if not ids: raise RuntimeError("Không tạo được công việc từ lịch.")
                            store.update_schedule(item["id"],job_ids=ids)
                            start_worker(ids,item["delay_seconds"],item["id"])
                        except Exception as e:
                            store.update_schedule(item["id"],"error",error=str(e)); log(f"Lỗi lịch: {e}")
                        break
        except Exception as e: log(f"Lỗi kiểm tra lịch: {e}")
        time.sleep(2)
threading.Thread(target=schedule_loop,daemon=True).start()

@app.get("/")
def index(): return send_from_directory("static","index.html")
@app.get("/api/status")
def status():
    jobs=store.all(1000)
    counts={s:sum(1 for j in jobs if j.status==s) for s in core.LABELS}
    return jsonify({"running":state["running"],"stop":state["stop"],"counts":counts,
        "jobs":[job_dict(j) for j in jobs[:100]],"logs":state["logs"][-250:],
        "groups":get_groups(),"schedules":store.list_schedules(),"library":store.list_library_posts()})
@app.post("/api/upload")
def upload():
    files=request.files.getlist("files"); saved=[]
    if len(files)>core.MAX_IMAGES: return jsonify(error=f"Tối đa {core.MAX_IMAGES} ảnh/bài."),400
    for f in files:
        ext=Path(f.filename or "").suffix.lower()
        if ext not in ALLOWED: return jsonify(error="Chỉ nhận JPG, PNG, WEBP."),400
        name=f"{uuid.uuid4().hex}{ext}"; dest=UPLOADS/name; f.save(dest)
        if dest.stat().st_size>500*1024*1024:
            dest.unlink(missing_ok=True)
            return jsonify(error=f"Ảnh {f.filename} lớn hơn 500 MB."),400
        saved.append(str(dest.resolve()))
    return jsonify(images=saved)
@app.get("/api/image")
def image_file():
    p=Path(request.args.get("path","")).resolve()
    if not p.is_file() or not str(p).startswith(str(HOME.resolve())): return "",404
    return send_file(p)
@app.post("/api/draft")
def draft():
    d=request.json or {}; core.save_draft(d.get("content",""),d.get("groups",""),d.get("images",[])); return jsonify(ok=True)
@app.get("/api/draft")
def get_draft(): return jsonify(core.load_draft())
@app.post("/api/start")
def start():
    d=request.json or {}
    try:
        content=d.get("content",""); groups=core.clean_groups(d.get("groups","")); images=core.validate_images(d.get("images",[]))
        if not content.strip(): raise ValueError("Bạn chưa nhập nội dung chữ.")
        ids,dup=store.add_jobs(content,images,groups,repeat_key=f"manual-{uuid.uuid4().hex}")
        if not ids: return jsonify(ok=False,error="Bài đã có trong lịch sử.",duplicates=dup),409
        state["current_ids"]=ids
        start_worker(ids,manual=True)
        log(f"Bắt đầu lượt đăng: {len(ids)} nhóm, {len(images)} ảnh/nhóm.")
        return jsonify(ok=True,ids=ids,duplicates=dup)
    except Exception as e: return jsonify(error=str(e)),400
@app.post("/api/resume")
def resume():
    ids=[j.id for j in reversed(store.all(2000)) if j.status==core.QUEUED]
    if not ids:return jsonify(error="Không còn bài chưa đăng trong hàng chờ."),400
    try:start_worker(ids); return jsonify(ok=True,ids=ids)
    except Exception as e:return jsonify(error=str(e)),409
@app.post("/api/stop")
def stop(): state["stop"]=True; log("Đã yêu cầu dừng sau nhóm hiện tại."); return jsonify(ok=True)
@app.post("/api/login")
def login():
    with lock:
        if state["running"]:return jsonify(error="Đang có công việc chạy."),409
        state["running"]=True
    def run():
        try: core.login_browser(log)
        except Exception as e:log(f"Lỗi đăng nhập: {type(e).__name__}: {e}")
        finally:
            with lock: state["running"]=False
    threading.Thread(target=run,daemon=True).start(); return jsonify(ok=True)
@app.post("/api/install")
def install():
    with lock:
        if state["running"]:return jsonify(error="Đang có công việc chạy."),409
        state["running"]=True
    def run():
        try:
            py=sys.executable
            for cmd in ([py,"-m","pip","install","--upgrade","playwright","Pillow"],[py,"-m","playwright","install","chromium"]):
                log("Chạy: "+" ".join(cmd))
                p=subprocess.run(cmd,capture_output=True,text=True,timeout=900)
                for line in (p.stdout+p.stderr).splitlines()[-12:]:log(line[:300])
                if p.returncode:raise RuntimeError("Lệnh cài đặt thất bại.")
            log("Cài đặt Playwright và Chromium hoàn tất.")
        except Exception as e:log("Lỗi cài đặt: "+str(e))
        finally:
            with lock: state["running"]=False
    threading.Thread(target=run,daemon=True).start(); return jsonify(ok=True)
@app.post("/api/groups")
def save_groups():
    d=request.json or {}; raw=d.get("groups","")
    try: urls=core.clean_groups(raw)
    except Exception as e:return jsonify(error=str(e)),400
    store.set_app_state("web_groups_text","\n".join(urls)); return jsonify(groups=urls)
def get_groups(): return (store.get_app_state("web_groups_text") or "").splitlines()
@app.get("/api/groups")
def groups():return jsonify(groups=get_groups())
@app.get("/api/export/groups")
def export_groups():
    data="\n".join(get_groups()).encode("utf-8-sig")
    return send_file(io.BytesIO(data),as_attachment=True,download_name="danh_sach_nhom_facebook.txt")
@app.get("/api/export/jobs")
def export_jobs():
    rows=store.all(10000); s=io.StringIO(); w=csv.writer(s)
    w.writerow(["ID","Nhóm","Trạng thái","Ghi chú","Thời điểm tạo","Thời điểm nhấn Đăng","Liên kết","Nội dung"])
    for j in rows:w.writerow([j.id,j.group_url,core.LABELS.get(j.status,j.status),j.note,j.created_at,j.sent_intent_at,j.permalink,j.content])
    return send_file(io.BytesIO(s.getvalue().encode("utf-8-sig")),as_attachment=True,download_name="lich_su_dang_bai.csv")
@app.post("/api/open-data")
def open_data():
    try:
        if sys.platform=="win32": os.startfile(str(HOME))
        elif sys.platform=="darwin": subprocess.Popen(["open",str(HOME)])
        else: subprocess.Popen(["xdg-open",str(HOME)])
        return jsonify(ok=True)
    except Exception as e:return jsonify(error=str(e)),500

@app.get("/api/backup")
def backup():
    dest=io.BytesIO()
    temp_db=HOME/f"backup_tmp_{uuid.uuid4().hex}.sqlite3"
    try:
        src=sqlite3.connect(core.DB_PATH,timeout=15); dst=sqlite3.connect(temp_db)
        try: src.backup(dst)
        finally: dst.close(); src.close()
        with zipfile.ZipFile(dest,"w",zipfile.ZIP_DEFLATED) as z:
            z.write(temp_db,"cong_viec.sqlite3")
            for folder in ("thu_vien_anh","lich_hen_anh","web_uploads"):
                p=HOME/folder
                if p.exists():
                    for f in p.rglob("*"):
                        if f.is_file():z.write(f,str(Path(folder)/f.relative_to(p)))
            if core.DRAFT_PATH.exists():z.write(core.DRAFT_PATH,"ban_nhap.json")
            manifest={"application":"FB Auto Post Web","backup_created_at":core.now(),
                      "includes":["database","draft_if_available","saved_post_images","scheduled_images","uploaded_images"],
                      "excludes":["Chromium browser profile and Facebook login session"],
                      "restore_note":"Stop the web application, back up the current FB_Auto_Post_V4 folder, then restore the ZIP contents into that folder. Do not replace the browser profile."}
            z.writestr("backup_info.json",json.dumps(manifest,ensure_ascii=False,indent=2))
        dest.seek(0)
        return send_file(dest,as_attachment=True,download_name="FB_Auto_Post_sao_luu.zip")
    finally:
        try: temp_db.unlink(missing_ok=True)
        except OSError: pass

@app.post("/api/jobs/<int:jid>/confirm")
def confirm(jid):
    try:store.confirm(jid);return jsonify(ok=True)
    except Exception as e:return jsonify(error=str(e)),400
@app.post("/api/jobs/<int:jid>/retry")
def retry(jid):
    if store.retry_before_submit(jid):
        try:start_worker([jid]);return jsonify(ok=True)
        except Exception as e:return jsonify(error=str(e)),409
    return jsonify(error="Chỉ thử lại bài lỗi trước khi nhấn Đăng."),400
@app.post("/api/jobs/<int:jid>/skip")
def skip(jid):
    store.skip(jid);return jsonify(ok=True)
@app.get("/api/jobs/<int:jid>")
def job_detail(jid):
    try:return jsonify(job=job_dict(store.get(jid)))
    except Exception:return jsonify(error="Không tìm thấy bài."),404
@app.post("/api/jobs/<int:jid>/reuse")
def reuse_job(jid):
    try:
        job=store.get(jid)
        try: selected_time=datetime.fromisoformat(job.created_at)
        except Exception: selected_time=None
        related=[]
        for candidate in store.all(limit=2000):
            if candidate.content!=job.content or candidate.images!=job.images: continue
            try: candidate_time=datetime.fromisoformat(candidate.created_at)
            except Exception: continue
            if selected_time is not None and abs((candidate_time-selected_time).total_seconds())>300: continue
            related.append(candidate)
        related.sort(key=lambda x:x.id)
        groups=list(dict.fromkeys(x.group_url for x in related)) or [job.group_url]
        return jsonify(payload={"content":job.content,"groups":"\n".join(groups),
                                "images":[p for p in job.images if Path(p).is_file()]})
    except Exception as e:return jsonify(error=str(e)),404

@app.get("/api/jobs/<int:jid>/evidence")
def job_evidence(jid):
    try:
        job=store.get(jid)
        folder=Path(job.evidence_path)
        if not folder.is_dir(): return jsonify(error="Chưa có thư mục bằng chứng."),404
        dest=io.BytesIO()
        with zipfile.ZipFile(dest,"w",zipfile.ZIP_DEFLATED) as z:
            for f in folder.rglob("*"):
                if f.is_file():z.write(f,f.relative_to(folder))
        dest.seek(0)
        return send_file(dest,as_attachment=True,download_name=f"bang_chung_bai_{jid}.zip")
    except Exception as e:return jsonify(error=str(e)),404

@app.post("/api/library")
def library_save():
    d=request.json or {}
    try:
        pid=store.save_library_post(d.get("title",""),d.get("content",""),d.get("groups",""),d.get("images",[]))
        return jsonify(id=pid)
    except Exception as e:return jsonify(error=str(e)),400
@app.get("/api/library/<int:pid>")
def library_get(pid):
    try:return jsonify(post=store.get_library_post(pid))
    except Exception:return jsonify(error="Không tìm thấy bài mẫu."),404
@app.delete("/api/library/<int:pid>")
def library_delete(pid):
    store.delete_library_post(pid);return jsonify(ok=True)
@app.post("/api/library/<int:pid>/load")
def library_load(pid):
    try:return jsonify(post=store.get_library_post(pid))
    except Exception as e:return jsonify(error=str(e)),404
@app.post("/api/schedules")
def schedule_add():
    d=request.json or {}
    try:
        when=f"{d.get('date','')} {d.get('time','')}"
        sid=store.add_schedule(d.get("title",""),d.get("content",""),d.get("groups",""),d.get("images",[]),when,int(d.get("delay",15)))
        return jsonify(id=sid)
    except Exception as e:return jsonify(error=str(e)),400
@app.delete("/api/schedules/<sid>")
def schedule_cancel(sid):
    if store.cancel_schedule(sid):return jsonify(ok=True)
    return jsonify(error="Chỉ hủy được lịch đang ở trạng thái Đã hẹn."),400
@app.patch("/api/schedules/<sid>")
def schedule_edit(sid):
    d=request.json or {}
    try:
        when=f"{d.get('date','')} {d.get('time','')}"
        if store.reschedule(sid,when):return jsonify(ok=True)
        return jsonify(error="Không thể chỉnh lịch này."),400
    except Exception as e:return jsonify(error=str(e)),400
@app.get("/api/activity")
def activity():return jsonify(items=store.list_activity())
@app.delete("/api/activity")
def activity_clear():store.clear_activity();state["logs"]=[];return jsonify(ok=True)
@app.get("/api/settings")
def settings():
    return jsonify({"theme":store.get_app_state("web_theme") or "light","delay":store.get_app_state("web_delay") or "15","autocontinue":store.get_app_state("web_autocontinue") or "1"})
@app.post("/api/settings")
def settings_save():
    d=request.json or {}
    for k in ("theme","delay","autocontinue"):
        if k in d:store.set_app_state("web_"+k,str(d[k]))
    return jsonify(ok=True)
if __name__=="__main__":
    print("FB Auto Post Web: http://127.0.0.1:5000")
    app.run(host="127.0.0.1",port=5000,debug=False,threaded=True)
