(() => {
  const status = document.getElementById('status');
  const canvas = document.getElementById('canvas');
  const empty = document.getElementById('empty');
  const dropzone = document.getElementById('dropzone');
  const anim = document.getElementById('animation');
  const team = document.getElementById('team');
  const zoom = document.getElementById('zoom');
  const wire = document.getElementById('wire');
  const play = document.getElementById('play');
  const reset = document.getElementById('reset');
  const infoEl = document.getElementById('modelInfo');
  const fileInput = document.getElementById('files');
  const effectRow = document.getElementById('effectRow');
  const effectSelect = document.getElementById('effectSelect');
  const sceneInfo = document.getElementById('sceneInfo');
  const loadingOverlay = document.getElementById('loadingOverlay');
  const loadingText = document.getElementById('loadingText');
  const pageParams = new URLSearchParams(location.search);
  if (pageParams.get('compact') === '1') document.body.classList.add('compact');

  let loadGeneration = 0;
  let loadingScene = false;
  const parsedModelCache = new Map();
  const sessionFileCache = new Map();
  const sessionSceneCache = new Map();
  const CACHE_LIMIT = 8;
  function lruGet(cache,key){
    if(!cache.has(key)) return null;
    const value=cache.get(key); cache.delete(key); cache.set(key,value); return value;
  }
  function lruSet(cache,key,value){
    if(cache.has(key)) cache.delete(key);
    cache.set(key,value);
    while(cache.size>CACHE_LIMIT) cache.delete(cache.keys().next().value);
    return value;
  }
  let model = null, renderer = null, gl = null, playing = true, last = 0;
  let modelLevelOfDetail = 0;
  let phi = Math.PI * .25, theta = Math.PI * .12, distance = 300, targetZ = 60;
  let scenePanX = 0, scenePanZ = 0;
  let dragging = false, dragButton = -1, lastX = 0, lastY = 0;
  let renderLoopStarted = false;
  let sceneMode = 'model';
  let sceneMeta = null;
  let effectEntries = [];
  let actorEntries = [];
  let abilityTestStart = 0;
  let abilityTestSettled = false;
  let abilityAutoReplay = true;
  const {mat4, vec3, quat} = window.glMatrix || {};

  function fail(msg) {
    status.textContent = msg; status.style.color = '#ff7b7b';
    if (empty) {
      const b=empty.querySelector('b'), span=empty.querySelector('span');
      if (b) b.textContent='ERRO 3D';
      if (span) span.textContent=String(msg||'Falha ao renderizar modelo.').slice(0,240);
      empty.style.display='';
    }
  }
  function ok(msg) { status.textContent = msg; status.style.color = ''; }
  function showLoading(msg='Carregando modelo…') { if(loadingText) loadingText.textContent=msg; if(loadingOverlay) loadingOverlay.hidden=false; }
  function hideLoading() { if(loadingOverlay) loadingOverlay.hidden=true; }
  function report(level,event,detail={}) {
    try {
      fetch('/viewer_log',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({level,event,detail}),keepalive:true}).catch(()=>{});
    } catch (_) {}
  }
  function currentSessionParams(){
    const hash=new URLSearchParams((location.hash||'').replace(/^#/,''));
    return hash.get('sid') ? hash : pageParams;
  }
  function currentRendererSet(){
    const out=new Set(); if(renderer) out.add(renderer); actorEntries.forEach(e=>e?.renderer&&out.add(e.renderer)); effectEntries.forEach(e=>e?.renderer&&out.add(e.renderer)); return out;
  }
  function destroyRendererSet(set,keep=new Set()){
    for(const r of set||[]){ if(!r || keep.has(r)) continue; try{ r.destroy?.(); }catch(err){ console.warn('renderer destroy',err); } }
  }
  function releaseCurrentRenderers(){
    const old=currentRendererSet();
    model=null; renderer=null; actorEntries=[]; effectEntries=[];
    destroyRendererSet(old);
  }
  function clearViewer(){
    const old=currentRendererSet();
    model=null; renderer=null; actorEntries=[]; effectEntries=[]; sceneMeta=null; sceneMode='model'; abilityTestStart=0;
    destroyRendererSet(old);
    effectRow.hidden=true; sceneInfo.textContent=''; infoEl.textContent=''; empty.style.display='';
    anim.innerHTML=''; anim.add(new Option('Animação','0'));
    ok('Selecione um modelo.');
  }
  function isCurrentLoad(generation){ return generation==null || generation===loadGeneration; }
  function libsReady() {
    if (!window.war3model || !mat4) {
      fail('Viewer dependency ausente. Rode setup_viewer.py com internet.');
      return false;
    }
    return true;
  }
  const norm = s => (s || '').replace(/\\/g,'/').split('/').pop().toLowerCase();
  const ext = s => (s.split('.').pop() || '').toLowerCase();
  function colorVec(hex) {
    return vec3.fromValues(parseInt(hex.slice(1,3),16)/255, parseInt(hex.slice(3,5),16)/255, parseInt(hex.slice(5,7),16)/255);
  }
  function cameraQuat(pos, target) {
    const projected = vec3.fromValues(pos[0], pos[1], 0);
    const delta = vec3.create(); vec3.subtract(delta, pos, target);
    vec3.normalize(projected, projected); vec3.normalize(delta, delta);
    const q = quat.create(), v = quat.create();
    quat.rotationTo(q, vec3.fromValues(1,0,0), projected);
    quat.rotationTo(v, projected, delta); quat.mul(q, v, q); return q;
  }
  function resize() {
    const rawDpr = window.devicePixelRatio || 1;
    const dpr = document.body.classList.contains('compact') ? Math.min(rawDpr,1.5) : rawDpr;
    const r = canvas.getBoundingClientRect();
    const w = Math.max(1, Math.round(r.width*dpr)), h = Math.max(1, Math.round(r.height*dpr));
    if (canvas.width !== w || canvas.height !== h) { canvas.width=w; canvas.height=h; }
  }
  function ensureGL() {
    if (gl) return;
    gl = canvas.getContext('webgl2', {alpha:true, antialias:true}) || canvas.getContext('webgl', {alpha:true, antialias:true});
    if (!gl) throw new Error('WebGL indisponível neste navegador');
    gl.clearColor(0.055,0.063,0.082,1); gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL);
  }
  function resetCamera() {
    if (!model) return;
    const min = model.Info?.MinimumExtent || [-100,-100,0], max = model.Info?.MaximumExtent || [100,100,200];
    const dx=max[0]-min[0], dy=max[1]-min[1], dz=max[2]-min[2];
    const radius=Math.max(50, Math.sqrt(dx*dx+dy*dy+dz*dz)*.7);
    targetZ=(min[2]+max[2])/2; distance=Math.min(2000,Math.max(80,radius*2.25));
    scenePanX=0; scenePanZ=0;
    if (sceneMode === 'ability_test') distance = Math.max(distance, 430);
    phi=Math.PI*.24; theta=Math.PI*.10; zoom.value=String(Math.round(distance));
  }
  function fillAnimationSelect() {
    anim.innerHTML='';
    const seqs=model?.Sequences || [];
    if (!seqs.length) anim.add(new Option('Sem animações','0'));
    seqs.forEach((s,i)=>anim.add(new Option(s.Name || `Sequence ${i}`,String(i))));
    if (seqs.length && renderer) renderer.setSequence(0);
  }
  function parseDDS(buf) {
    const dv=new DataView(buf);
    if(buf.byteLength<128 || dv.getUint32(0,true)!==0x20534444) throw new Error('DDS magic inválido');
    const width=dv.getUint32(16,true), height=dv.getUint32(12,true), mipCount=Math.max(1,dv.getUint32(28,true)||1);
    const pfFlags=dv.getUint32(80,true);
    let code=String.fromCharCode(dv.getUint8(84),dv.getUint8(85),dv.getUint8(86),dv.getUint8(87));
    let format=null, block=0, off=128, srgb=false, uncompressed=false, bgra=false;
    const legacy={DXT1:['bc1',8],DXT3:['bc2',16],DXT5:['bc3',16],ATI2:['bc5',16],BC5U:['bc5',16]};
    if(code==='DX10'){
      if(buf.byteLength<148) throw new Error('DDS DX10 truncado');
      const dxgi=dv.getUint32(128,true); off=148;
      const dx10={
        71:['bc1',8,false],72:['bc1',8,true],74:['bc2',16,false],75:['bc2',16,true],
        77:['bc3',16,false],78:['bc3',16,true],83:['bc5',16,false],84:['bc5',16,false],
        98:['bc7',16,false],99:['bc7',16,true]
      };
      if(dxgi===28 || dxgi===87){ format='rgba8'; uncompressed=true; bgra=dxgi===87; }
      else if(dx10[dxgi]) [format,block,srgb]=dx10[dxgi];
      else throw new Error('DDS DX10/DXGI não suportado: '+dxgi);
      code='DX10:'+dxgi;
    } else if(legacy[code]) { [format,block]=legacy[code]; }
    else if((pfFlags&0x4)===0 && dv.getUint32(88,true)===32){ format='rgba8'; uncompressed=true; bgra=true; }
    else throw new Error('DDS FourCC não suportado: '+code);
    const images=[]; let w=width,h=height;
    for(let i=0;i<mipCount;i++){
      const length=uncompressed ? w*h*4 : Math.max(1,Math.ceil(w/4))*Math.max(1,Math.ceil(h/4))*block;
      if(off+length>buf.byteLength) break;
      images.push({offset:off,length,shape:{width:w,height:h}}); off+=length; w=Math.max(1,w>>1); h=Math.max(1,h>>1);
    }
    if(!images.length) throw new Error('DDS sem mip válido');
    return {format,width,height,images,srgb,uncompressed,bgra,code};
  }
  function decodeDDSImageData(buf,dds,imageIndex=0){
    const image=dds.images[imageIndex];
    const src=new Uint8Array(buf,image.offset,image.length);
    const out=new Uint8ClampedArray(image.shape.width*image.shape.height*4);
    const count=Math.min(src.length,out.length);
    if(dds.bgra){
      for(let i=0;i+3<count;i+=4){out[i]=src[i+2];out[i+1]=src[i+1];out[i+2]=src[i];out[i+3]=src[i+3];}
    } else out.set(src.subarray(0,count));
    return new ImageData(out,image.shape.width,image.shape.height);
  }
  function decodeTGA(buf) {
    const dv=new DataView(buf), u=new Uint8Array(buf); if(buf.byteLength<18) throw new Error('TGA truncado');
    const idLen=u[0], cmap=u[1], type=u[2], width=dv.getUint16(12,true), height=dv.getUint16(14,true), bpp=u[16], desc=u[17];
    if(cmap) throw new Error('TGA com color map ainda não suportado');
    if(![2,3,10,11].includes(type)) throw new Error('TGA type não suportado: '+type);
    const bytes=Math.ceil(bpp/8); if(![1,3,4].includes(bytes)) throw new Error('TGA bpp não suportado: '+bpp);
    let pos=18+idLen, pixel=0; const count=width*height, raw=new Uint8ClampedArray(count*4);
    function readPx(){
      if(pos+bytes>u.length) throw new Error('TGA truncado nos pixels');
      let r,g,b,a=255;
      if(bytes===1){r=g=b=u[pos++];}
      else {b=u[pos++];g=u[pos++];r=u[pos++];if(bytes===4)a=u[pos++];}
      return [r,g,b,a];
    }
    function put(px){const o=pixel++*4;raw[o]=px[0];raw[o+1]=px[1];raw[o+2]=px[2];raw[o+3]=px[3];}
    if(type===2||type===3){while(pixel<count)put(readPx());}
    else {while(pixel<count){const h=u[pos++],run=(h&127)+1;if(h&128){const px=readPx();for(let i=0;i<run&&pixel<count;i++)put(px);}else for(let i=0;i<run&&pixel<count;i++)put(readPx());}}
    const top=(desc&0x20)!==0; const out=new Uint8ClampedArray(raw.length);
    for(let y=0;y<height;y++){const sy=top?y:height-1-y;out.set(raw.subarray(sy*width*4,(sy+1)*width*4),y*width*4);}
    return new ImageData(out,width,height);
  }
  async function applyTexture(targetRenderer, file, textureName) {
    try {
      const e=ext(file.name);
      if (e==='blp') {
        const buf=await file.arrayBuffer(); const blp=war3model.decodeBLP(buf);
        targetRenderer.setTextureImageData(textureName, blp.mipmaps.map((_m,i)=>war3model.getBLPImageData(blp,i)));
        return true;
      }
      if (e==='tga') {
        targetRenderer.setTextureImageData(textureName,[decodeTGA(await file.arrayBuffer())]); return true;
      }
      if (e==='dds') {
        const buf=await file.arrayBuffer(), dds=parseDDS(buf);
        if(dds.uncompressed){
          targetRenderer.setTextureImageData(textureName,dds.images.map((_m,i)=>decodeDDSImageData(buf,dds,i)));
          return true;
        }
        const s3tc=gl.getExtension('WEBGL_compressed_texture_s3tc')||gl.getExtension('MOZ_WEBGL_compressed_texture_s3tc')||gl.getExtension('WEBKIT_WEBGL_compressed_texture_s3tc');
        const s3tcSrgb=gl.getExtension('WEBGL_compressed_texture_s3tc_srgb');
        const rgtc=gl.getExtension('EXT_texture_compression_rgtc');
        const bptc=gl.getExtension('EXT_texture_compression_bptc');
        let fmt=null;
        if(dds.format==='bc1')fmt=dds.srgb?(s3tcSrgb?.COMPRESSED_SRGB_S3TC_DXT1_EXT||s3tc?.COMPRESSED_RGBA_S3TC_DXT1_EXT):s3tc?.COMPRESSED_RGBA_S3TC_DXT1_EXT;
        if(dds.format==='bc2')fmt=dds.srgb?(s3tcSrgb?.COMPRESSED_SRGB_ALPHA_S3TC_DXT3_EXT||s3tc?.COMPRESSED_RGBA_S3TC_DXT3_EXT):s3tc?.COMPRESSED_RGBA_S3TC_DXT3_EXT;
        if(dds.format==='bc3')fmt=dds.srgb?(s3tcSrgb?.COMPRESSED_SRGB_ALPHA_S3TC_DXT5_EXT||s3tc?.COMPRESSED_RGBA_S3TC_DXT5_EXT):s3tc?.COMPRESSED_RGBA_S3TC_DXT5_EXT;
        if(dds.format==='bc5')fmt=rgtc?.COMPRESSED_RED_GREEN_RGTC2_EXT;
        if(dds.format==='bc7')fmt=dds.srgb?(bptc?.COMPRESSED_SRGB_ALPHA_BPTC_UNORM_EXT||bptc?.COMPRESSED_RGBA_BPTC_UNORM_EXT):bptc?.COMPRESSED_RGBA_BPTC_UNORM_EXT;
        if(!fmt) throw new Error('Browser/GPU sem extensão para '+dds.format+' ('+dds.code+')');
        targetRenderer.setTextureCompressedImage(textureName,fmt,buf,dds); return true;
      }
      if (['png','jpg','jpeg'].includes(e)) {
        const url=URL.createObjectURL(file); const img=new Image();
        await new Promise((res,rej)=>{img.onload=res;img.onerror=rej;img.src=url});
        targetRenderer.setTextureImage(textureName,img); URL.revokeObjectURL(url); return true;
      }
      return false;
    } catch (err) { console.warn('texture',file.name,err); report('warning','texture_error',{file:file.name,texture:textureName,error:String(err?.message||err)}); return false; }
  }
  function textureRoles(parsed){
    const roles=new Map();
    const add=(id,role)=>{if(Number.isInteger(id)&&id>=0){if(!roles.has(id))roles.set(id,new Set());roles.get(id).add(role);}};
    for(const mat of parsed?.Materials||[]) for(const layer of mat?.Layers||[]){
      add(layer.TextureID,'diffuse'); add(layer.NormalTextureID,'normal'); add(layer.ORMTextureID,'orm');
      add(layer.EmissiveTextureID,'emissive'); add(layer.TeamColorTextureID,'team'); add(layer.ReflectionsTextureID,'reflection');
    }
    return roles;
  }
  function fallbackTextureForRoles(roles){
    let rgba=[255,255,255,255];
    if(roles?.has('normal')) rgba=[128,128,255,255];
    else if(roles?.has('orm')) rgba=[255,160,0,0];
    else if(roles?.has('emissive')) rgba=[0,0,0,255];
    return new ImageData(new Uint8ClampedArray(rgba),1,1);
  }
  function modelSkinDiagnostics(parsed){
    const used=new Set(); let wideGeosets=0,maxNode=0,skinGeosets=0;
    for(const g of parsed?.Geosets||[]){
      const skin=g?.SkinWeights; if(!skin)continue; skinGeosets++; if(skin instanceof Uint16Array)wideGeosets++;
      for(let i=0;i+7<skin.length;i+=8) for(let k=0;k<4;k++) if(skin[i+4+k]>0){const n=skin[i+k];used.add(n);if(n>maxNode)maxNode=n;}
    }
    return {skinGeosets,wideGeosets,activeBones:used.size,maxNode};
  }
  function preferredLevelOfDetail(parsed) {
    // Classic/SD MDX v800 models frequently store their visible geosets at LOD 0.
    // Passing Reforged's LOD 2 unconditionally makes the renderer skip every
    // classic geoset, leaving a perfectly parsed/animated model as a black box.
    const levels=(parsed?.Geosets || [])
      .map(g=>g?.LevelOfDetail)
      .filter(v=>Number.isFinite(v));
    if (!levels.length) return 0;
    const unique=[...new Set(levels)];
    const version=Number(parsed?.Version || 0);
    if (version && version<=800) return unique.includes(0) ? 0 : Math.min(...unique);
    if (unique.includes(2)) return 2;
    if (unique.includes(0)) return 0;
    return Math.max(...unique);
  }
  async function buildRenderable(modelFile, allFiles, generation=null) {
    ensureGL();
    if(!isCurrentLoad(generation)) return null;
    const sid=currentSessionParams().get('sid') || 'local';
    const parseKey=`${sid}|${modelFile.name}|${modelFile.size}|${modelFile.lastModified||0}`;
    let parsed=lruGet(parsedModelCache,parseKey);
    if(parsed){
      report('info','model_parse_cache_hit',{sid,file:modelFile.name,bytes:modelFile.size});
    }else{
      const parseStarted=performance.now();
      parsed = ext(modelFile.name)==='mdx' ? war3model.parseMDX(await modelFile.arrayBuffer()) : war3model.parseMDL(await modelFile.text());
      lruSet(parsedModelCache,parseKey,parsed);
      report('info','model_parse_cache_miss',{sid,file:modelFile.name,bytes:modelFile.size,ms:Math.round(performance.now()-parseStarted)});
    }
    if(!isCurrentLoad(generation)) return null;
    // A few custom/classic MDL assets are valid static models but contain no
    // Sequences block. The bundled renderer assumes sequence 0 exists in its
    // constructor, so synthesize a harmless one-frame Stand sequence instead
    // of crashing on animationInfo.Interval.
    let syntheticStaticSequence=false;
    if (!(parsed?.Sequences || []).length) {
      parsed.Sequences=[{Name:'Stand (static)',Interval:[0,1],NonLooping:0,MoveSpeed:0,Rarity:0,SyncPoint:0}];
      syntheticStaticSequence=true;
      report('warning','model_static_sequence_injected',{sid,file:modelFile.name});
    }
    const buildStarted=performance.now();
    const rendererStarted=performance.now();
    const skinDiag=modelSkinDiagnostics(parsed);
    const glDiag={webgl2:typeof WebGL2RenderingContext!=='undefined'&&gl instanceof WebGL2RenderingContext,maxVertexUniformVectors:Number(gl.getParameter(gl.MAX_VERTEX_UNIFORM_VECTORS)||0),renderer:String(gl.getParameter(gl.RENDERER)||''),vendor:String(gl.getParameter(gl.VENDOR)||'')};
    report('info','model_render_caps',{sid,file:modelFile.name,...skinDiag,...glDiag});
    const r = new war3model.ModelRenderer(parsed); r.initGL(gl); r.setTeamColor(colorVec(team.value));
    const rendererMs=performance.now()-rendererStarted;
    const byName=new Map(allFiles.filter(f=>!['mdx','mdl'].includes(ext(f.name))).map(f=>[norm(f.name),f]));
    const roles=textureRoles(parsed);
    let loaded=0, requested=0, fallbackTextures=0;
    const textureStarted=performance.now();
    for (let textureIndex=0; textureIndex<(parsed.Textures||[]).length; textureIndex++) {
      const tex=parsed.Textures[textureIndex];
      if(!isCurrentLoad(generation)){ try{r.destroy?.();}catch(_){} return null; }
      if (!tex.Image) continue; requested++;
      const requestedName=norm(tex.Image);
      let f=byName.get(requestedName);
      // Reforged/DE MDX files frequently reference logical .tif/.blp names while
      // the CASC payload is DDS. The engine aliases them automatically; mirror
      // that behavior in the standalone viewer.
      if (!f) {
        const stem=requestedName.replace(/\.(?:tif|tga|blp|dds)$/i,'');
        for (const alt of [`${stem}.dds`,`${stem}.blp`,`${stem}.tga`,`${stem}.tif`]) {
          f=byName.get(alt); if (f) break;
        }
      }
      const didLoad=!!(f && await applyTexture(r,f,tex.Image));
      if (didLoad) loaded++;
      else {
        try { r.setTextureImageData(tex.Image,[fallbackTextureForRoles(roles.get(textureIndex))]); fallbackTextures++; } catch (_) {}
      }
    }
    const textureMs=performance.now()-textureStarted;
    if ((parsed.Sequences||[]).length) r.setSequence(0);
    const lod=preferredLevelOfDetail(parsed);
    report('info','renderable_ready',{sid,file:modelFile.name,ms:Math.round(performance.now()-buildStarted),rendererMs:Math.round(rendererMs),textureMs:Math.round(textureMs),texturesLoaded:loaded,texturesRequested:requested,fallbackTextures,geosets:(parsed.Geosets||[]).length,lod,syntheticStaticSequence,...skinDiag});
    return {model:parsed, renderer:r, file:modelFile, loaded, requested, fallbackTextures, levelOfDetail:lod, syntheticStaticSequence, skinDiag};
  }
  function startRenderLoop() {
    if (renderLoopStarted) return;
    renderLoopStarted=true; last=performance.now(); requestAnimationFrame(frame);
  }
  function setModelModeUI() {
    sceneMode='model'; sceneMeta=null; effectEntries=[]; abilityTestStart=0;
    effectRow.hidden=true; sceneInfo.textContent='';
  }
  async function loadFiles(files, explicitModelName=null, generation=null) {
    if (!libsReady()) return;
    const list=[...files];
    const mf=list.find(f => explicitModelName ? f.name===explicitModelName : ['mdx','mdl'].includes(ext(f.name)));
    if (!mf) { fail('Nenhum MDX/MDL encontrado.'); report('error','model_missing',{explicitModelName,files:list.map(f=>f.name)}); return; }
    ok(`Carregando ${mf.name}…`);
    const started=performance.now();
    loadingScene=true;
    releaseCurrentRenderers();
    try {
      const main=await buildRenderable(mf,list,generation);
      if(!main || !isCurrentLoad(generation)) return;
      setModelModeUI();
      model=main.model; renderer=main.renderer; modelLevelOfDetail=main.levelOfDetail;
      fillAnimationSelect(); resetCamera(); empty.style.display='none';
      infoEl.textContent=`${model.Version || '?'} · ${(model.Geosets||[]).length} geosets · ${(model.Sequences||[]).length} anim.`;
      ok(`${mf.name} · ${main.loaded}/${main.requested} texturas resolvidas${main.fallbackTextures?` · ${main.fallbackTextures} fallback`:''}`);
      report('info','model_ready',{file:mf.name,ms:Math.round(performance.now()-started),texturesLoaded:main.loaded,texturesRequested:main.requested,geosets:(model.Geosets||[]).length,sequences:(model.Sequences||[]).length,lod:modelLevelOfDetail});
      startRenderLoop();
    } catch (err) {
      console.error(err);
      const glError=gl?gl.getError():null;
      fail(`Erro ao abrir modelo: ${err.message || err}`);
      report('error','model_open_error',{file:mf.name,error:String(err?.stack||err),glError,maxVertexUniformVectors:gl?Number(gl.getParameter(gl.MAX_VERTEX_UNIFORM_VECTORS)||0):0});
    }
    finally { loadingScene=false; }
  }
  function bestCastSequence(hint='') {
    const seqs=model?.Sequences || [];
    if (!seqs.length) return 0;
    const h=(hint||'').toLowerCase().replace(/[^a-z0-9_-]+/g,' ').trim();
    const tokens=h.split(/[ ,]+/).filter(Boolean);
    let idx=-1;
    if (tokens.length) idx=seqs.findIndex(s=>tokens.every(t=>(s.Name||'').toLowerCase().includes(t)));
    if (idx<0 && tokens.length) idx=seqs.findIndex(s=>tokens.some(t=>(s.Name||'').toLowerCase().includes(t)));
    if (idx<0) {
      // Prefer real cast/spell sequences. A broad regex used to pick Attack 1
      // simply because it appeared before Spell in many custom hero MDXs.
      const priorities=[/^spell\b/i,/spell/i,/channel/i,/slam/i,/cast/i,/attack.*spell/i,/attack/i,/stand/i];
      for (const rx of priorities) { idx=seqs.findIndex(s=>rx.test(s.Name||'')); if(idx>=0) break; }
    }
    return Math.max(0,idx);
  }
  function bestStandSequence(targetModel=model) {
    const seqs=targetModel?.Sequences || [];
    if (!seqs.length) return 0;
    let idx=seqs.findIndex(s=>/^stand\b/i.test(s.Name||''));
    if (idx<0) idx=seqs.findIndex(s=>/stand/i.test(s.Name||''));
    return Math.max(0,idx);
  }
  function restartEffectSequences() {
    for (const entry of effectEntries) {
      const seqs=entry.model?.Sequences||[];
      if (seqs.length) entry.renderer.setSequence(0);
    }
    for (const entry of actorEntries) {
      const seqs=entry.model?.Sequences||[];
      if (seqs.length) entry.renderer.setSequence(bestStandSequence(entry.model));
    }
  }
  function triggerAbilityTest() {
    if (sceneMode!=='ability_test' || !renderer) return;
    const idx=bestCastSequence(sceneMeta?.cast_animation||'');
    if ((model?.Sequences||[]).length) {
      renderer.setSequence(idx); anim.value=String(idx);
    }
    restartEffectSequences();
    renderer.update(0); actorEntries.forEach(e=>e.renderer.update(0)); effectEntries.forEach(e=>e.renderer.update(0));
    abilityTestStart=performance.now(); abilityTestSettled=false;
    playing=true; play.textContent='Pause';
    const seqName=(model?.Sequences||[])[idx]?.Name || `Sequence ${idx}`;
    sceneInfo.textContent=`${seqName} · ${actorEntries.length} alvo(s) · ${effectEntries.length} efeito(s) · botão direito move`;
    ok(`Testando ${sceneMeta?.ability_name || sceneMeta?.ability_id || 'habilidade'} em ${sceneMeta?.hero_name || 'herói'} · ${seqName}`);
  }
  async function loadAbilityScene(files, scene, generation=null) {
    if (!libsReady()) return;
    const started=performance.now();
    loadingScene=true;
    releaseCurrentRenderers();
    const list=[...files];
    const byFile=new Map(list.map(f=>[f.name,f]));
    const heroFile=byFile.get(scene.hero_model);
    if (!heroFile) { loadingScene=false; fail('Modelo do herói de teste não chegou ao viewer.'); return; }
    ok(`Carregando herói de teste ${scene.hero_name || scene.hero_id || ''}…`);
    try {
      sceneMode='ability_test'; sceneMeta=scene; effectEntries=[]; actorEntries=[];
      const main=await buildRenderable(heroFile,list,generation);
      if(!main || !isCurrentLoad(generation)) return;
      model=main.model; renderer=main.renderer; modelLevelOfDetail=main.levelOfDetail;
      fillAnimationSelect(); empty.style.display='none';
      let actorTextureLoaded=0, actorTextureRequested=0;
      for (const item of scene.actors || []) {
        const f=byFile.get(item.model); if(!f) continue;
        try {
          const built=await buildRenderable(f,list,generation);
          if(!built || !isCurrentLoad(generation)) return;
          actorTextureLoaded+=built.loaded; actorTextureRequested+=built.requested;
          actorEntries.push({...item,...built});
        } catch (err) { console.warn('scene actor model',item,err); }
      }
      let effectTextureLoaded=0, effectTextureRequested=0;
      for (const item of scene.effects || []) {
        const f=byFile.get(item.model); if(!f) continue;
        try {
          const built=await buildRenderable(f,list,generation);
          if(!built || !isCurrentLoad(generation)) return;
          effectTextureLoaded+=built.loaded; effectTextureRequested+=built.requested;
          effectEntries.push({...item,...built});
        } catch (err) { console.warn('effect model',item,err); }
      }
      effectSelect.innerHTML='';
      effectSelect.add(new Option('Todos os efeitos','all'));
      effectSelect.add(new Option('Nenhum','none'));
      effectEntries.forEach((e,i)=>effectSelect.add(new Option(e.label || `Efeito ${i+1}`,String(i))));
      effectRow.hidden=false;
      const demoKind=(scene.demo_kind || 'self').toLowerCase();
      sceneInfo.textContent=`${scene.hero_name || scene.hero_id || 'Herói'} · ${actorEntries.length} alvo(s) · ${effectEntries.length} efeito(s) · ${demoKind}`;
      infoEl.textContent=`TESTE ${scene.ability_id || ''} · ${(model.Sequences||[]).length} anim. do herói · ${actorEntries.length} ator(es) · ${effectEntries.length} efeito(s)`;
      resetCamera();
      ok(`${scene.ability_name || scene.ability_id || 'Spell'} · herói ${main.loaded}/${main.requested} tex · atores ${actorTextureLoaded}/${actorTextureRequested} tex · efeitos ${effectTextureLoaded}/${effectTextureRequested} tex`);
      report('info','ability_scene_ready',{ability:scene.ability_id||'',ms:Math.round(performance.now()-started),actors:actorEntries.length,effects:effectEntries.length});
      startRenderLoop(); triggerAbilityTest();
    } catch (err) { console.error(err); fail(`Erro no teste da habilidade: ${err.message || err}`); report('error','ability_scene_error',{ability:scene.ability_id||'',error:String(err?.stack||err)}); }
    finally { loadingScene=false; }
  }
  async function loadAbilityFxScene(files, scene, generation=null) {
    if (!libsReady()) return;
    const started=performance.now();
    loadingScene=true;
    releaseCurrentRenderers();
    const list=[...files];
    const byFile=new Map(list.map(f=>[f.name,f]));
    try {
      sceneMode='ability_fx'; sceneMeta=scene; effectEntries=[]; actorEntries=[];
      let effectTextureLoaded=0, effectTextureRequested=0;
      for (const item of scene.effects || []) {
        const f=byFile.get(item.model); if(!f) continue;
        try {
          const built=await buildRenderable(f,list,generation);
          if(!built || !isCurrentLoad(generation)) return;
          effectTextureLoaded+=built.loaded; effectTextureRequested+=built.requested;
          effectEntries.push({...item,...built});
        } catch (err) { console.warn('ability FX model',item,err); report('warning','ability_fx_model_skipped',{ability:scene.ability_id||'',label:item.label||'',model:item.model||'',error:String(err?.message||err)}); }
      }
      if (!effectEntries.length) { fail('Nenhum modelo 3D de efeito chegou ao viewer.'); return; }
      model=effectEntries[0].model; renderer=effectEntries[0].renderer; modelLevelOfDetail=effectEntries[0].levelOfDetail;
      fillAnimationSelect(); empty.style.display='none';
      effectSelect.innerHTML='';
      effectSelect.add(new Option('Todos os FX','all'));
      effectEntries.forEach((e,i)=>effectSelect.add(new Option(e.label || `FX ${i+1}`,String(i))));
      effectSelect.value='all'; effectRow.hidden=false;
      sceneInfo.textContent=`${effectEntries.length} FX · somente modelos da habilidade`;
      infoEl.textContent=`SKILL ${scene.ability_id || ''} · ${effectEntries.length} FX 3D`;
      abilityTestStart=performance.now(); abilityTestSettled=false;
      restartEffectSequences();
      resetCamera();
      ok(`${scene.ability_name || scene.ability_id || 'Skill'} · ${effectEntries.length} FX · ${effectTextureLoaded}/${effectTextureRequested} texturas`);
      report('info','ability_fx_ready',{ability:scene.ability_id||'',ms:Math.round(performance.now()-started),effects:effectEntries.length,texturesLoaded:effectTextureLoaded,texturesRequested:effectTextureRequested});
      startRenderLoop();
    } catch (err) { console.error(err); fail(`Erro ao abrir FX da habilidade: ${err.message || err}`); report('error','ability_fx_error',{ability:scene.ability_id||'',error:String(err?.stack||err)}); }
    finally { loadingScene=false; }
  }

  function shouldRenderEffect(index, entry, now) {
    const v=effectSelect.value;
    if (v==='none') return false;
    if (!(v==='all' || !v || Number(v)===index)) return false;
    if (sceneMode==='ability_fx') return true;
    if (sceneMode!=='ability_test') return true;
    if (!abilityTestStart) return false;
    const elapsed=Math.max(0,now-abilityTestStart);
    const kind=(entry?.placement||'caster').toLowerCase();
    const demo=(sceneMeta?.demo_kind||'self').toLowerCase();
    // Aura art must remain visible long enough to inspect. Other spells use a
    // short staged timeline and are automatically replayed below.
    if (demo==='aura') {
      if (kind==='missile') return elapsed>=120;
      return true;
    }
    if (kind==='missile') return elapsed>=120 && elapsed<=1750;
    if (kind==='target') return elapsed>=430 && elapsed<=2450;
    if (kind==='area') return elapsed>=360 && elapsed<=2700;
    return elapsed<=2300;
  }
  function actorOffset(role) {
    const rows={ally:[210,0,0], enemy:[210,0,0], support:[150,0,0], self:[0,0,0]};
    return rows[(role||'ally').toLowerCase()] || [210,0,0];
  }
  function targetOffset() {
    const role=(sceneMeta?.target_role || 'self').toLowerCase();
    if (role==='ally') return actorOffset('ally');
    if (role==='enemy') return actorOffset('enemy');
    return [0,0,0];
  }
  function effectOffset(entry, now) {
    const kind=(entry.placement||'caster').toLowerCase();
    const target=targetOffset();
    if (kind==='missile') {
      const elapsed=abilityTestStart ? Math.max(0,now-abilityTestStart-120) : 0;
      const t=Math.max(0,Math.min(1,elapsed/1050));
      return [25+(target[0]-25)*t,0,75+Math.sin(Math.PI*t)*95];
    }
    if (kind==='target') return [target[0],target[1],target[2]];
    if (kind==='area') return [target[0],target[1],target[2]+2];
    return [0,0,0];
  }
  function render(now) {
    if (loadingScene || !renderer || !model) return;
    const dt=Math.min(100, now-last || 0); last=now;
    if (playing) {
      if (sceneMode!=='ability_fx') renderer.update(dt);
      if (sceneMode==='ability_fx') {
        effectEntries.forEach(e=>e.renderer.update(dt));
        // Effect models are often short one-shot sequences. Restart them so the
        // preview stays alive without a separate "test ability" action.
        if (abilityTestStart && now-abilityTestStart>=3200) {
          restartEffectSequences(); abilityTestStart=now;
        }
      }
      if (sceneMode==='ability_test') {
        actorEntries.forEach(e=>e.renderer.update(dt));
        effectEntries.forEach(e=>e.renderer.update(dt));
        if (abilityTestStart && !abilityTestSettled && now-abilityTestStart>=1850) {
          const stand=bestStandSequence(model);
          if ((model?.Sequences||[]).length) { renderer.setSequence(stand); anim.value=String(stand); }
          abilityTestSettled=true;
        }
        // Spell Lab is a demonstrator, not a one-shot screenshot. Re-run the
        // cast automatically so particles/projectiles are visible even if the
        // user looks at the panel a few seconds after selecting the ability.
        // Auras replay more slowly because their art remains continuously shown.
        if (abilityAutoReplay && abilityTestStart) {
          const demo=(sceneMeta?.demo_kind||'self').toLowerCase();
          const cycle=demo==='aura' ? 5200 : 3300;
          if (now-abilityTestStart>=cycle) triggerAbilityTest();
        }
      }
    }
    const p=mat4.create(), mv=mat4.create(); mat4.perspective(p,Math.PI/4,canvas.width/canvas.height,.1,5000);
    const target=vec3.fromValues(sceneMode==='ability_test'?70:0,0,targetZ);
    const pos=vec3.fromValues(Math.cos(theta)*Math.cos(phi)*distance,Math.cos(theta)*Math.sin(phi)*distance,targetZ+Math.sin(theta)*distance);
    mat4.lookAt(mv,pos,target,vec3.fromValues(0,0,1));
    gl.viewport(0,0,canvas.width,canvas.height); gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    const cameraQ=cameraQuat(pos,target), lightPos=vec3.fromValues(300,-500,700), lightColor=vec3.fromValues(1,1,1);
    const mainMv=mat4.clone(mv); mat4.translate(mainMv,mainMv,[scenePanX,0,scenePanZ]);
    renderer.setCamera(pos,cameraQ); renderer.setLightPosition(lightPos); renderer.setLightColor(lightColor);
    if (sceneMode!=='ability_fx') renderer.render(mainMv,p,{wireframe:wire.checked,levelOfDetail:modelLevelOfDetail});
    if (sceneMode==='ability_fx') {
      effectEntries.forEach((entry,index)=>{
        if(!shouldRenderEffect(index,entry,now)) return;
        const emv=mat4.clone(mainMv);
        entry.renderer.setCamera(pos,cameraQ); entry.renderer.setLightPosition(lightPos); entry.renderer.setLightColor(lightColor);
        entry.renderer.render(emv,p,{wireframe:wire.checked,levelOfDetail:entry.levelOfDetail});
      });
    }
    if (sceneMode==='ability_test') {
      actorEntries.forEach((entry)=>{
        const amv=mat4.clone(mainMv); const off=actorOffset(entry.role); mat4.translate(amv,amv,off);
        entry.renderer.setCamera(pos,cameraQ); entry.renderer.setLightPosition(lightPos); entry.renderer.setLightColor(lightColor);
        entry.renderer.render(amv,p,{wireframe:wire.checked,levelOfDetail:entry.levelOfDetail});
      });
      effectEntries.forEach((entry,index)=>{
        if(!shouldRenderEffect(index,entry,now)) return;
        const emv=mat4.clone(mainMv); const off=effectOffset(entry,now); mat4.translate(emv,emv,off);
        entry.renderer.setCamera(pos,cameraQ); entry.renderer.setLightPosition(lightPos); entry.renderer.setLightColor(lightColor);
        entry.renderer.render(emv,p,{wireframe:wire.checked,levelOfDetail:entry.levelOfDetail});
      });
    }
  }
  function frame(now){ render(now); requestAnimationFrame(frame); }

  anim.addEventListener('change',()=>{ if(renderer) renderer.setSequence(Number(anim.value)||0); });
  effectSelect.addEventListener('change',()=>{
    if (sceneMode!=='ability_fx' || !effectEntries.length) return;
    const idx=effectSelect.value==='all' ? 0 : Math.max(0,Number(effectSelect.value)||0);
    const entry=effectEntries[Math.min(idx,effectEntries.length-1)];
    if (!entry) return;
    model=entry.model; renderer=entry.renderer; modelLevelOfDetail=entry.levelOfDetail; fillAnimationSelect(); resetCamera();
    sceneInfo.textContent=`${effectEntries.length} FX · ${effectSelect.value==='all'?'todos':(entry.label || `FX ${idx+1}`)}`;
  });
  team.addEventListener('input',()=>{
    const c=colorVec(team.value); if(renderer) renderer.setTeamColor(c); actorEntries.forEach(e=>e.renderer.setTeamColor(c)); effectEntries.forEach(e=>e.renderer.setTeamColor(c));
  });
  zoom.addEventListener('input',()=>distance=Number(zoom.value));
  play.addEventListener('click',()=>{playing=!playing;play.textContent=playing?'Pause':'Play';});
  reset.addEventListener('click',resetCamera);
  fileInput.addEventListener('change',()=>loadFiles(fileInput.files));
  canvas.addEventListener('contextmenu',e=>e.preventDefault());
  canvas.addEventListener('mousedown',e=>{if(e.button>2)return;dragging=true;dragButton=e.button;lastX=e.clientX;lastY=e.clientY;e.preventDefault();});
  window.addEventListener('mouseup',()=>{dragging=false;dragButton=-1;});
  window.addEventListener('mousemove',e=>{
    if(!dragging)return; const dx=e.clientX-lastX,dy=e.clientY-lastY;lastX=e.clientX;lastY=e.clientY;
    if(dragButton===2){
      // Right mouse = move the model/whole spell scene without moving the camera.
      const factor=Math.max(.12,distance/700); scenePanX+=dx*.55*factor; scenePanZ-=dy*.55*factor;
    } else if(dragButton===1){
      targetZ+=dy*.25;
    } else {
      phi-=dx*.01;theta=Math.max(-1.53,Math.min(1.53,theta+dy*.01));
    }
  });
  canvas.addEventListener('wheel',e=>{distance=Math.max(20,Math.min(2000,distance*(1+Math.sign(e.deltaY)*.1)));zoom.value=String(Math.round(distance));e.preventDefault();},{passive:false});
  ['dragenter','dragover'].forEach(n=>dropzone.addEventListener(n,e=>{e.preventDefault();dropzone.classList.add('drop-active')}));
  ['dragleave','drop'].forEach(n=>dropzone.addEventListener(n,e=>{e.preventDefault();dropzone.classList.remove('drop-active')}));
  dropzone.addEventListener('drop',e=>loadFiles(e.dataTransfer.files));

  async function autoload(){
    const params=currentSessionParams();
    const modelUrl=params.get('model'), sid=params.get('sid');
    const generation=++loadGeneration;
    if(!sid || !modelUrl){
      clearViewer(); hideLoading();
      return;
    }
    const wanted=decodeURIComponent(modelUrl).split('/').pop();
    const started=performance.now();
    showLoading(`Carregando ${wanted}…`);
    report('info','session_load_start',{sid,model:wanted,scene:params.get('scene')==='1',generation});
    try{
      let files, totalBytes=0;
      const cachedFiles=lruGet(sessionFileCache,sid);
      if(cachedFiles){
        files=cachedFiles.files; totalBytes=cachedFiles.totalBytes;
        report('info','session_files_cache_hit',{sid,files:files.length,bytes:totalBytes});
      }else{
        const mr=await fetch('/session_manifest.json?sid='+encodeURIComponent(sid),{cache:'no-store'});
        if(!mr.ok) throw new Error('manifest HTTP '+mr.status);
        const manifest=await mr.json();
        if(!isCurrentLoad(generation)) return;
        files=[];
        for(const rel of manifest.files||[]){
          if(!isCurrentLoad(generation)) return;
          const safeRel=rel.split('/').map(encodeURIComponent).join('/');
          const r=await fetch('/session/'+encodeURIComponent(sid)+'/'+safeRel,{cache:'force-cache'});
          if(!r.ok) throw new Error('asset HTTP '+r.status+': '+rel);
          const b=await r.blob(); totalBytes+=b.size;
          if(!isCurrentLoad(generation)) return;
          files.push(new File([b],rel.split('/').pop(),{type:b.type,lastModified:0}));
        }
        lruSet(sessionFileCache,sid,{files,totalBytes});
        report('info','session_files_cache_miss',{sid,files:files.length,bytes:totalBytes});
      }
      if(!isCurrentLoad(generation)) return;
      if(params.get('scene')==='1'){
        let scene=lruGet(sessionSceneCache,sid);
        if(!scene){
          const sr=await fetch('/session_scene.json?sid='+encodeURIComponent(sid),{cache:'no-store'});
          if(!sr.ok) throw new Error('scene HTTP '+sr.status);
          const scenePayload=await sr.json();
          scene=scenePayload.scene||{};
          lruSet(sessionSceneCache,sid,scene);
        }
        if(!isCurrentLoad(generation)) return;
        if(scene.mode==='ability_fx'){ await loadAbilityFxScene(files,scene,generation); }
        else if(scene.mode==='ability_test'){ await loadAbilityScene(files,scene,generation); }
        else { await loadFiles(files,wanted,generation); }
      } else {
        await loadFiles(files,wanted,generation);
      }
      if(!isCurrentLoad(generation)) return;
      report('info','session_load_ready',{sid,model:wanted,ms:Math.round(performance.now()-started),files:files.length,bytes:totalBytes,generation});
    }catch(err){
      if(!isCurrentLoad(generation)) return;
      console.error(err); fail('Falha no autoload: '+(err?.message||err));
      report('error','autoload_error',{sid,model:wanted,error:String(err?.stack||err),generation});
    }finally{
      if(isCurrentLoad(generation)) hideLoading();
    }
  }
  window.addEventListener('hashchange',autoload);
  window.addEventListener('resize',resize);
  if(window.ResizeObserver){ try{ new ResizeObserver(resize).observe(canvas); }catch(_){} }
  resize(); autoload();
})();
