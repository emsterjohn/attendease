(() => {
  const resultBox=document.getElementById("scan-result");
  const confirmation=document.getElementById("scan-confirmation");
  const nameField=document.getElementById("scanned-name");
  const idField=document.getElementById("scanned-id");
  const confirmButton=document.getElementById("confirm-scan");
  const cancelButton=document.getElementById("cancel-scan");
  const flipButton=document.getElementById("flip-camera");
  if(!window.Html5Qrcode){resultBox.className="alert alert-danger mt-3";resultBox.textContent="The QR scanner could not load. Check your internet connection.";return;}
  const scanner=new Html5Qrcode("reader");
  let cameras=[],cameraIndex=0,scannerRunning=false,busy=false,pendingToken=null,liveStudents=[];
  function showResult(message,kind="info"){resultBox.className=\`alert alert-\${kind} mt-3\`;resultBox.textContent=message;resultBox.classList.remove("d-none");}
  async function startScanner(){
    if(scannerRunning||busy)return;
    try{
      if(!cameras.length)cameras=await Html5Qrcode.getCameras();
      if(!cameras.length){showResult("No camera was found on this device.","danger");if(flipButton)flipButton.disabled=true;return;}
      if(flipButton)flipButton.disabled=cameras.length<2;
      await scanner.start(cameras[cameraIndex].id,{fps:10,qrbox:{width:240,height:240}},onScanSuccess);
      scannerRunning=true;
    }catch(_){showResult("Camera could not start. Allow camera permission and use HTTPS or localhost.","danger");}
  }
  async function stopScanner(){if(!scannerRunning)return;try{await scanner.stop();}catch(_){}scannerRunning=false;}
  async function onScanSuccess(decodedText){
    if(busy||pendingToken)return;busy=true;pendingToken=decodedText;await stopScanner();
    try{
      const response=await fetch("/api/scan/preview",{method:"POST",headers:{"Content-Type":"application/json"},credentials:"same-origin",body:JSON.stringify({token:decodedText})});
      const data=await response.json();
      if(!response.ok||!data.ok){pendingToken=null;showResult(data.message||"This QR code could not be identified.","danger");busy=false;await startScanner();return;}
      nameField.textContent=data.full_name;idField.textContent=data.student_id;confirmation.classList.remove("d-none");
      if(data.already_recorded)showResult(\`\${data.full_name} has already been marked \${data.existing_status} today at \${data.existing_time}.\`,"warning");
      else showResult(\`QR code identified: \${data.full_name}. Confirm the student's identity below.\`,"success");
    }catch(_){pendingToken=null;showResult("Could not contact the server. Please try again.","danger");busy=false;await startScanner();}
  }
  function renderStudents(){
    const body=document.getElementById("scan-student-rows");if(!body)return;
    const query=(document.getElementById("scan-student-search")?.value||"").toLowerCase();
    const rows=liveStudents.filter(s=>\`\${s.full_name} \${s.student_id}\`.toLowerCase().includes(query));body.replaceChildren();
    if(!rows.length){const tr=document.createElement("tr"),td=document.createElement("td");td.colSpan=4;td.className="empty-state";td.textContent="No matching students found.";tr.appendChild(td);body.appendChild(tr);return;}
    rows.forEach(s=>{
      const tr=document.createElement("tr");
      [s.full_name,s.student_id].forEach(v=>{const td=document.createElement("td");td.textContent=v||"—";tr.appendChild(td);});
      const status=s.status||"Not Yet Scanned",td=document.createElement("td"),pill=document.createElement("span");pill.className="status-pill "+(status==="Not Yet Scanned"?"unrecorded":status.toLowerCase());pill.textContent=status;td.appendChild(pill);tr.appendChild(td);
      const time=document.createElement("td");time.textContent=s.check_in||"—";tr.appendChild(time);body.appendChild(tr);
    });
  }
  async function refreshList(){
    try{
      const response=await fetch("/api/dashboard/live",{credentials:"same-origin",cache:"no-store"});if(!response.ok)return;
      const data=await response.json();liveStudents=data.students||[];renderStudents();
      const c=data.counts||{};
      document.getElementById("scan-present-count").textContent=\`Present \${c.present||0}\`;
      document.getElementById("scan-late-count").textContent=\`Late \${c.late||0}\`;
      document.getElementById("scan-absent-count").textContent=\`Absent \${c.absent||0}\`;
      document.getElementById("scan-pending-count").textContent=\`Not scanned \${Math.max(0,(c.total||0)-(c.present||0)-(c.late||0)-(c.absent||0))}\`;
    }catch(_){}
  }
  confirmButton.addEventListener("click",async()=>{
    if(!pendingToken||confirmButton.disabled)return;confirmButton.disabled=true;
    try{
      const response=await fetch("/api/scan",{method:"POST",headers:{"Content-Type":"application/json"},credentials:"same-origin",body:JSON.stringify({token:pendingToken})});
      const data=await response.json();confirmation.classList.add("d-none");
      if(response.ok&&data.ok)showResult(\`Attendance recorded. \${data.full_name} is \${data.status}. Scan time: \${data.check_in}.\`,"success");
      else showResult(\`\${data.full_name||nameField.textContent}: \${data.message||"Attendance could not be recorded."}\`,"warning");
      pendingToken=null;busy=false;await refreshList();window.setTimeout(startScanner,1500);
    }catch(_){showResult("Could not save attendance. Please try again.","danger");pendingToken=null;busy=false;await startScanner();}
    finally{confirmButton.disabled=false;}
  });
  cancelButton.addEventListener("click",async()=>{pendingToken=null;confirmation.classList.add("d-none");busy=false;showResult("Scan cancelled. You can scan another student.","secondary");await startScanner();});
  flipButton.addEventListener("click",async()=>{
    if(cameras.length<2){showResult("This device has only one available camera.","warning");return;}
    const restart=scannerRunning;await stopScanner();cameraIndex=(cameraIndex+1)%cameras.length;
    if(restart)await startScanner();showResult("Camera switched successfully.","info");
  });
  document.getElementById("scan-student-search")?.addEventListener("input",renderStudents);
  refreshList();window.setInterval(()=>{if(document.visibilityState==="visible")refreshList();},5000);startScanner();
})();
