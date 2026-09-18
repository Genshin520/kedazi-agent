import {parseSSE} from "./sse.mjs";
const $ = id => document.getElementById(id);
let origin = sessionStorage.getItem("kedazi-origin") || "http://127.0.0.1:8000";
let threadId = null, busy = false, controller = null, previewURL = null, connected = false;
const modeDescriptions = {hint:"先给一点提示，把思考的空间留给你。", check:"从你的步骤出发，找到第一个需要调整的地方。", explain:"把事件、公式和计算连起来，完整走一遍。"};
$("api-origin").value = origin;
function status(text, error=false) { $("status").textContent=text; $("status").classList.toggle("error",error); }
function node(tag, text, cls) { const el=document.createElement(tag); if(text!=null)el.textContent=text;if(cls)el.className=cls;return el; }
async function api(path, options={}) {
  const headers = {...(options.body instanceof FormData ? {} : {"Content-Type":"application/json"}), ...options.headers};
  const response = await fetch(origin+path,{...options,headers});
  if(!response.ok) {
    const data = await response.json().catch(()=>({}));
    throw new Error(typeof data.detail==="string" ? data.detail : "请求失败（"+response.status+"）");
  }
  return response;
}
function setBusy(value) {
  busy=value;
  for (const el of document.querySelectorAll("#send,#new-thread,#file,.thread,.examples button,#settings-open,#profile-open,#remove-file")) el.disabled=value;
  $("stop").hidden=!value;
  $("question").disabled=value;
}
function message(role,text) {
  const article=node("article",null,"message "+role);
  article.append(node("span",role==="user"?"你":"课搭子","role"));
  const body=node("div",text,"body"); article.append(body); $("messages").append(article);
  return {article,body};
}
function renderEvidence(results=[]) {
  const docs = new Map();
  for(const result of results) for(const doc of result.documents||[]) docs.set(doc.id,doc);
  $("evidence").replaceChildren();
  if(!docs.size) { $("evidence").append(node("p","本轮还没有可展示的课程依据。","muted")); return; }
  for(const doc of docs.values()) {
    const details=node("details",null,"evidence-card");
    details.append(node("summary",doc.title.split(" / ").at(-1)));
    details.append(node("p",doc.content));
    details.append(node("div",doc.source+" · "+doc.id,"ranks"));
    details.append(node("div","RRF "+doc.rrf_score.toFixed(4)+" · 精排 "+(doc.rerank_score?.toFixed(3)??"未执行"),"ranks"));
    $("evidence").append(details);
  }
}
function renderProcess(result) {
  $("process").replaceChildren();
  for(const retrieval of result.retrieval||[]) {
    const row=node("div",null,"trace");
    row.append(node("strong",retrieval.rerank_model || "课程检索"));
    row.append(node("span","原问题："+retrieval.query+"\n检索词："+(retrieval.rewritten_query||retrieval.query)+"\n向量召回："+(retrieval.dense_ids||[]).join(", ")+"\nBM25召回："+(retrieval.bm25_ids||[]).join(", ")));
    $("process").append(row);
  }
  for(const tool of result.tools||[]) {
    const row=node("div",null,"trace");row.append(node("strong",tool.name));
    row.append(node("span",tool.status+" · "+tool.duration_ms+" ms"));$("process").append(row);
  }
  if(result.observation) {
    const row=node("div",null,"trace");row.append(node("strong","图片识别 · 请核对"));
    row.append(node("p",result.observation));$("process").append(row);
  }
  $("process").append(node("p","引用校验只验证来源 ID 存在，不等于答案正确性证明。","muted"));
}
async function refreshThreads() {
  const threads = await (await api("/api/threads")).json();
  $("threads").replaceChildren();
  for(const thread of threads) {
    const button=node("button",thread.title,"thread");
    button.classList.toggle("active",thread.id===threadId);button.disabled=busy;
    button.onclick=()=>openThread(thread.id).catch(e=>status(e.message,true));$("threads").append(button);
  }
}
async function openThread(id) {
  if(busy)return;
  const history=await(await api("/api/threads/"+id)).json();
  threadId=id; $("messages").replaceChildren(); removeFile(); renderEvidence([]);
  for(const turn of history) {message("user",turn.question);message("assistant",turn.result.answer);}
  if(history.length) {const result=history.at(-1).result;renderEvidence(result.retrieval);renderProcess(result);}
  await refreshThreads();status("已恢复讨论记录。");
}
async function newThread() {
  if(busy)return;
  threadId=(await(await api("/api/threads",{method:"POST"})).json()).id;
  $("messages").replaceChildren();message("assistant","新的一页。把题目或你的思路告诉我吧。");
  renderEvidence([]);$("process").replaceChildren();removeFile();await refreshThreads();$("question").focus();
}
async function connect() {
  const health=await(await api("/health")).json();
  await refreshThreads(); connected=true;
  $("connection").textContent="已连接 · "+health.text_model;
  $("connection").classList.add("ready");
  status("已连接，可以提问或上传题目图片。");
}
function removeFile() {
  if(previewURL)URL.revokeObjectURL(previewURL);
  previewURL=null;$("file").value="";$("attachment").hidden=true;
}
$("file").onchange=()=>{
  const file=$("file").files[0];if(!file)return;
  if(file.size>5*1024*1024||!["image/jpeg","image/png","image/webp"].includes(file.type)) {removeFile();status("请选择 5 MB 以内的 JPEG、PNG、WEBP 图片。",true);return;}
  if(previewURL)URL.revokeObjectURL(previewURL);
  previewURL=URL.createObjectURL(file);$("preview").src=previewURL;$("filename").textContent=file.name;$("attachment").hidden=false;
};
$("remove-file").onclick=removeFile;
$("chat-form").onsubmit=async event=>{
  event.preventDefault();if(busy)return;
  const question=$("question").value.trim();if(!question){status("请先写下问题，或说明希望如何分析图片。",true);return;}
  if(!connected){$("settings-dialog").showModal();return;}
  let assistant, completed=false;
  controller=new AbortController();setBusy(true);
  try {
    if(!threadId)threadId=(await(await api("/api/threads",{method:"POST",signal:controller.signal})).json()).id;
    let imageId=null;
    const file=$("file").files[0];
    if(file){status("正在上传题目图片…");const form=new FormData();form.append("file",file);imageId=(await(await api("/api/uploads",{method:"POST",body:form,signal:controller.signal})).json()).image_id;}
    $("welcome")?.remove();message("user",question+(file?"\n[已附题目图片]":""));assistant=message("assistant","");
    $("question").value="";removeFile();renderEvidence([]);$("process").replaceChildren();
    const response=await api("/api/chat",{method:"POST",signal:controller.signal,body:JSON.stringify({
      thread_id:threadId,message:question,image_id:imageId,mode:document.querySelector('[name="mode"]:checked').value
    })});
    const retrieval=[];
    for await(const {event,data} of parseSSE(response.body)){
      if(event==="status")status(data.message);
      else if(event==="delta")assistant.body.textContent+=data.text;
      else if(event==="evidence"){retrieval.push(data);renderEvidence(retrieval);}
      else if(event==="observation")status("题目识别完成，正在查找课程依据。");
      else if(event==="error")throw new Error(data.message+" 请求号："+data.request_id);
      else if(event==="done"){
        completed=true;assistant.body.textContent=data.answer;renderEvidence(data.retrieval);renderProcess(data);
        status(data.invalid_citations.length?"回答已完成，存在未通过校验的引用，请核对。":"本轮已完成。可以继续追问，或展开右侧课程依据。");
      }
    }
    if(!completed)throw new Error("响应未完成，请重新提问。");
    await refreshThreads();
  } catch(error){
    const text=error.name==="AbortError"?"已停止。本轮可能未完成；若后续恢复失败，请新建讨论。":error.message;
    status(text,true);
    if(assistant&&!completed){assistant.article.classList.add("error");assistant.body.textContent+="\n\n[本轮未完成] "+text;}
  } finally {setBusy(false);controller=null;$("question").focus();}
};
$("stop").onclick=()=>controller?.abort();
$("question").onkeydown=e=>{if((e.ctrlKey||e.metaKey)&&e.key==="Enter"){e.preventDefault();$("chat-form").requestSubmit();}};
document.querySelectorAll('[name="mode"]').forEach(input=>input.onchange=()=>{$("mode-description").textContent=modeDescriptions[input.value];});
document.querySelectorAll("[data-question]").forEach(button=>button.onclick=()=>{$("question").value=button.dataset.question;$("question").focus();});
$("new-thread").onclick=()=>newThread().catch(e=>status(e.message,true));
$("settings-open").onclick=()=>$("settings-dialog").showModal();
document.querySelectorAll("[data-close]").forEach(button=>button.onclick=()=>$(button.dataset.close).close());
$("settings-form").onsubmit=async e=>{
  e.preventDefault();
  try {
    const url=new URL($("api-origin").value);
    if(!["http:","https:"].includes(url.protocol)||url.username||url.password)throw new Error("请输入有效的 HTTP(S) 地址");
    origin=url.origin;threadId=null;connected=false;
    sessionStorage.setItem("kedazi-origin",origin);
    await connect();$("messages").replaceChildren();renderEvidence([]);$("process").replaceChildren();$("settings-dialog").close();
  }catch(error){status(error.message,true);$("settings-dialog").close();}
};
$("profile-open").onclick=async()=>{
  try{const p=await(await api("/api/profile")).json();$("style").value=p.style;$("weak-topics").value=p.weak_topics.join("\n");$("profile-dialog").showModal();}
  catch(e){status(e.message,true);}
};
$("profile-form").onsubmit=async e=>{
  e.preventDefault();
  try{await api("/api/profile",{method:"PUT",body:JSON.stringify({style:$("style").value,weak_topics:$("weak-topics").value.split("\n").map(x=>x.trim()).filter(Boolean)})});$("profile-dialog").close();status("学习偏好已保存，新讨论也会使用。");}
  catch(error){$("profile-dialog").close();status(error.message,true);}
};
for(const name of ["evidence","process"])$("tab-"+name).onclick=()=>{
  for(const current of ["evidence","process"]){$(current).hidden=current!==name;$("tab-"+current).classList.toggle("active",current===name);$("tab-"+current).setAttribute("aria-pressed",String(current===name));}
};
connect().catch(()=>{connected=false;status("尚未连接后端。请启动服务，再打开“连接设置”。",true);});
