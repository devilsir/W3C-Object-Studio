from pathlib import Path
import tempfile
import urllib.request

from wc3injector.debuglog import configure_logging, install_exception_hooks, log_exception

LOGGER, _SESSION_LOG = configure_logging()
install_exception_hooks(LOGGER)

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "viewer" / "vendor"
FILES = {
    "war3-model.browser.js": "https://cdn.jsdelivr.net/npm/war3-model@4.0.1/dist/war3-model.browser.js",
    "gl-matrix-min.js": "https://cdn.jsdelivr.net/npm/gl-matrix@3.4.3/gl-matrix-min.js",
}


def fetch(url: str, dest: Path):
    req = urllib.request.Request(url, headers={"User-Agent": "WC3-Object-Studio/1.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = resp.read()
    if len(data) < 10_000:
        raise RuntimeError(f"download pequeno/inválido: {url} ({len(data)} bytes)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, dir=dest.parent, suffix=".tmp") as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    tmp_path.replace(dest)
    print(f"OK {dest.relative_to(ROOT)} ({len(data):,} bytes)")


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"war3-model 4.0.1 inesperado: {label} não encontrado")
    return text.replace(old, new, 1)


def patch_war3_model_de(dest: Path):
    """Patch war3-model 4.0.1 for Warcraft III patch 3.0 / Definitive Edition.

    Patch 3.0 widened MDX SKIN elements from uint8 to uint16 in the stock art
    trees. v3.10.7 added parser support; v3.10.8 switched to a compact dynamic
    bone palette so WebView2/WebGL can link the shader safely. v3.10.9 also
    hardens CAMS parsing because some stock Reforged HD models declare camera
    entry sizes that spill into the following top-level chunk (often EVTS).
    """
    text = dest.read_text(encoding="utf-8")
    if (
        "this.skinNodePalette = [];" in text
        and "HD/DE model needs ${this.nodeCapacity} active bones" in text
        and "Some stock Reforged HD models report a camera inclusive size" in text
    ):
        return

    # ---- Patch 3.0 wide SKIN parser / writer ----
    if "SkinWeightsElementBytes = wideSkin ? 2 : 1" not in text:
        old = """\t\tuint8Array(len) {\n\t\t\tconst res = new Uint8Array(len);\n\t\t\tfor (let i = 0; i < len; ++i) res[i] = this.uint8();\n\t\t\treturn res;\n\t\t}\n"""
        new = old + """\t\tuint16Array(len) {\n\t\t\tconst res = new Uint16Array(len);\n\t\t\tfor (let i = 0; i < len; ++i) res[i] = this.uint16();\n\t\t\treturn res;\n\t\t}\n\t\tpeekKeyword(pos) {\n\t\t\tif (pos < 0 || pos + 4 > this.length) return \"\";\n\t\t\treturn String.fromCharCode(this.uint[pos], this.uint[pos + 1], this.uint[pos + 2], this.uint[pos + 3]);\n\t\t}\n"""
        text = _replace_once(text, old, new, "leitor uint8Array")

        old = """\t\t\t\t} else if (keyword === \"SKIN\") {\n\t\t\t\t\tif (geoset.SkinWeights) throw new Error(\"Incorrect geoset, multiple SkinWeights\");\n\t\t\t\t\tconst len = state.int32();\n\t\t\t\t\tgeoset.SkinWeights = state.uint8Array(len);\n\t\t\t\t} else if (keyword === \"UVAS\") break;"""
        new = """\t\t\t\t} else if (keyword === \"SKIN\") {\n\t\t\t\t\tif (geoset.SkinWeights) throw new Error(\"Incorrect geoset, multiple SkinWeights\");\n\t\t\t\t\tconst len = state.int32();\n\t\t\t\t\tconst payloadPos = state.pos;\n\t\t\t\t\tconst nextAfter8 = state.peekKeyword(payloadPos + len);\n\t\t\t\t\tconst nextAfter16 = state.peekKeyword(payloadPos + len * 2);\n\t\t\t\t\tconst wideSkin = nextAfter16 === \"UVAS\" && nextAfter8 !== \"UVAS\";\n\t\t\t\t\tgeoset.SkinWeights = wideSkin ? state.uint16Array(len) : state.uint8Array(len);\n\t\t\t\t\tgeoset.SkinWeightsElementBytes = wideSkin ? 2 : 1;\n\t\t\t\t} else if (keyword === \"UVAS\") break;"""
        text = _replace_once(text, old, new, "bloco SKIN")
        text = text.replace(
            '(model.Version >= 900 && geoset.SkinWeights?.length ? 8 + geoset.SkinWeights.length : 0)',
            '(model.Version >= 900 && geoset.SkinWeights?.length ? 8 + geoset.SkinWeights.byteLength : 0)',
            1,
        )
        old = """\t\t\t\tif (geoset.SkinWeights && geoset.SkinWeights.length) {\n\t\t\t\t\tstream.keyword(\"SKIN\");\n\t\t\t\t\tstream.int32(geoset.SkinWeights.length);\n\t\t\t\t\tstream.uint8Array(geoset.SkinWeights);\n\t\t\t\t}"""
        new = """\t\t\t\tif (geoset.SkinWeights && geoset.SkinWeights.length) {\n\t\t\t\t\tstream.keyword(\"SKIN\");\n\t\t\t\t\tstream.int32(geoset.SkinWeights.length);\n\t\t\t\t\tif (geoset.SkinWeights instanceof Uint16Array) stream.uint16Array(geoset.SkinWeights);\n\t\t\t\t\telse stream.uint8Array(geoset.SkinWeights);\n\t\t\t\t}"""
        text = _replace_once(text, old, new, "writer SKIN")

        # Wide weights are still 0..255, merely stored in uint16. Feed raw
        # integer values to GLSL and normalize explicitly in the shader.
        weights = ' * aBoneWeight[0];\\n    sum += uNodesMatrices[int(aSkin[1])] * aBoneWeight[1];\\n    sum += uNodesMatrices[int(aSkin[2])] * aBoneWeight[2];\\n    sum += uNodesMatrices[int(aSkin[3])] * aBoneWeight[3];'
        weighted = ' * (aBoneWeight[0] / 255.0);\\n    sum += uNodesMatrices[int(aSkin[1])] * (aBoneWeight[1] / 255.0);\\n    sum += uNodesMatrices[int(aSkin[2])] * (aBoneWeight[2] / 255.0);\\n    sum += uNodesMatrices[int(aSkin[3])] * (aBoneWeight[3] / 255.0);'
        text = text.replace(weights, weighted, 2)
        old = """\t\t\t\t\tthis.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.skinWeightBuffer[i]);\n\t\t\t\t\tthis.gl.vertexAttribPointer(this.shaderProgramLocations.skinAttribute, 4, this.gl.UNSIGNED_BYTE, false, 8, 0);\n\t\t\t\t\tthis.gl.vertexAttribPointer(this.shaderProgramLocations.weightAttribute, 4, this.gl.UNSIGNED_BYTE, true, 8, 4);"""
        new = """\t\t\t\t\tthis.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.skinWeightBuffer[i]);\n\t\t\t\t\tconst wideSkin = geoset.SkinWeights instanceof Uint16Array;\n\t\t\t\t\tconst skinType = wideSkin ? this.gl.UNSIGNED_SHORT : this.gl.UNSIGNED_BYTE;\n\t\t\t\t\tconst skinStride = wideSkin ? 16 : 8;\n\t\t\t\t\tconst weightOffset = wideSkin ? 8 : 4;\n\t\t\t\t\tthis.gl.vertexAttribPointer(this.shaderProgramLocations.skinAttribute, 4, skinType, false, skinStride, 0);\n\t\t\t\t\tthis.gl.vertexAttribPointer(this.shaderProgramLocations.weightAttribute, 4, skinType, false, skinStride, weightOffset);"""
        text = _replace_once(text, old, new, "atributos SKIN WebGL")

    # ---- v3.10.9 Reforged HD camera-chunk hardening ----
    # A few stock HD models have camera inclusive sizes that extend beyond the
    # parent CAMS payload. war3-model 4.0.1 follows that size blindly, consumes
    # the next top-level tag (commonly EVTS), and throws "Incorrect camera chunk
    # data EVTS". Clamp camera entries to CAMS and treat unknown camera-only
    # extensions as optional instead of aborting the entire model.
    if "Some stock Reforged HD models report a camera inclusive size" not in text:
        old = """\tfunction parseCameras(model, state, size) {\n\t\tconst startPos = state.pos;\n\t\twhile (state.pos < startPos + size) {\n\t\t\tconst cameraStart = state.pos;\n\t\t\tconst cameraSize = state.int32();\n\t\t\tconst camera = {};\n\t\t\tcamera.Name = state.str(MODEL_CAMERA_NAME_LENGTH$1);\n\t\t\tcamera.Position = new Float32Array(3);\n\t\t\tcamera.Position[0] = state.float32();\n\t\t\tcamera.Position[1] = state.float32();\n\t\t\tcamera.Position[2] = state.float32();\n\t\t\tcamera.FieldOfView = state.float32();\n\t\t\tcamera.FarClip = state.float32();\n\t\t\tcamera.NearClip = state.float32();\n\t\t\tcamera.TargetPosition = new Float32Array(3);\n\t\t\tcamera.TargetPosition[0] = state.float32();\n\t\t\tcamera.TargetPosition[1] = state.float32();\n\t\t\tcamera.TargetPosition[2] = state.float32();\n\t\t\twhile (state.pos < cameraStart + cameraSize) {\n\t\t\t\tconst keyword = state.keyword();\n\t\t\t\tif (keyword === \"KCTR\") camera.Translation = state.animVector(AnimVectorType$1.FLOAT3);\n\t\t\t\telse if (keyword === \"KTTR\") camera.TargetTranslation = state.animVector(AnimVectorType$1.FLOAT3);\n\t\t\t\telse if (keyword === \"KCRL\") camera.Rotation = state.animVector(AnimVectorType$1.FLOAT1);\n\t\t\t\telse throw new Error(\"Incorrect camera chunk data \" + keyword);\n\t\t\t}\n\t\t\tmodel.Cameras.push(camera);\n\t\t}\n\t}\n"""
        new = """\tfunction parseCameras(model, state, size) {\n\t\tconst startPos = state.pos;\n\t\tconst chunkEnd = Math.min(state.length, startPos + size);\n\t\twhile (state.pos < chunkEnd) {\n\t\t\tconst cameraStart = state.pos;\n\t\t\tif (chunkEnd - state.pos < 4) {\n\t\t\t\tstate.pos = chunkEnd;\n\t\t\t\tbreak;\n\t\t\t}\n\t\t\tconst cameraSize = state.int32();\n\t\t\t// Some stock Reforged HD models report a camera inclusive size that\n\t\t\t// extends a few bytes beyond the parent CAMS chunk. The old parser\n\t\t\t// then consumed the next top-level tag (commonly EVTS) as if it were\n\t\t\t// a camera animation tag and aborted the entire model. Clamp each\n\t\t\t// camera to its parent chunk; cameras are optional for this viewer.\n\t\t\tconst declaredEnd = cameraStart + Math.max(4, cameraSize);\n\t\t\tconst cameraEnd = Math.min(chunkEnd, declaredEnd);\n\t\t\tif (cameraEnd - state.pos < 116) {\n\t\t\t\tstate.pos = cameraEnd;\n\t\t\t\tcontinue;\n\t\t\t}\n\t\t\tconst camera = {};\n\t\t\tcamera.Name = state.str(MODEL_CAMERA_NAME_LENGTH$1);\n\t\t\tcamera.Position = new Float32Array(3);\n\t\t\tcamera.Position[0] = state.float32();\n\t\t\tcamera.Position[1] = state.float32();\n\t\t\tcamera.Position[2] = state.float32();\n\t\t\tcamera.FieldOfView = state.float32();\n\t\t\tcamera.FarClip = state.float32();\n\t\t\tcamera.NearClip = state.float32();\n\t\t\tcamera.TargetPosition = new Float32Array(3);\n\t\t\tcamera.TargetPosition[0] = state.float32();\n\t\t\tcamera.TargetPosition[1] = state.float32();\n\t\t\tcamera.TargetPosition[2] = state.float32();\n\t\t\twhile (state.pos + 4 <= cameraEnd) {\n\t\t\t\tconst keyword = state.keyword();\n\t\t\t\tif (keyword === \"KCTR\") camera.Translation = state.animVector(AnimVectorType$1.FLOAT3);\n\t\t\t\telse if (keyword === \"KTTR\") camera.TargetTranslation = state.animVector(AnimVectorType$1.FLOAT3);\n\t\t\t\telse if (keyword === \"KCRL\") camera.Rotation = state.animVector(AnimVectorType$1.FLOAT1);\n\t\t\t\telse {\n\t\t\t\t\t// Unknown/extended camera data must never make geometry disappear.\n\t\t\t\t\tstate.pos = cameraEnd;\n\t\t\t\t\tbreak;\n\t\t\t\t}\n\t\t\t}\n\t\t\tif (state.pos < cameraEnd) state.pos = cameraEnd;\n\t\t\tmodel.Cameras.push(camera);\n\t\t}\n\t\tif (state.pos !== chunkEnd) state.pos = chunkEnd;\n\t}\n"""
        text = _replace_once(text, old, new, "parser CAMS/EVTS HD")

    # ---- v3.10.8 renderer hardening ----
    # Revert the unsafe global 512 palette from v3.10.7, or annotate upstream 254.
    text = text.replace(
        'var MAX_NODES = 512; // patch 3.0 DE rigs can exceed the legacy 8-bit bone-index range',
        'var MAX_NODES = 254; // SD/default sentinel; HD/DE uses a compact dynamic palette per model',
        1,
    )
    text = text.replace(
        'var MAX_NODES = 254;',
        'var MAX_NODES = 254; // SD/default sentinel; HD/DE uses a compact dynamic palette per model',
        1,
    ) if 'var MAX_NODES = 254; // SD/default sentinel' not in text else text

    old = """\tfunction getShader(gl, source, type) {\n\t\tconst shader = gl.createShader(type);\n\t\tgl.shaderSource(shader, source);\n\t\tgl.compileShader(shader);\n\t\tif (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {\n\t\t\talert(gl.getShaderInfoLog(shader));\n\t\t\treturn null;\n\t\t}\n\t\treturn shader;\n\t}\n"""
    if old in text:
        new = """\tfunction getShader(gl, source, type) {\n\t\tconst shader = gl.createShader(type);\n\t\tgl.shaderSource(shader, source);\n\t\tgl.compileShader(shader);\n\t\tif (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {\n\t\t\tconst info = gl.getShaderInfoLog(shader) || \"unknown shader compile error\";\n\t\t\tgl.deleteShader(shader);\n\t\t\tthrow new Error(`WebGL shader compile failed: ${info}`);\n\t\t}\n\t\treturn shader;\n\t}\n"""
        text = text.replace(old, new, 1)

    text = text.replace('\\n    mat4 sum;\\n\\n    // sum +=', '\\n    mat4 sum = mat4(0.0);\\n\\n    // sum +=', 2)

    if "this.skinNodePalette = [];" not in text:
        old = """\t\t\tthis.isHD = model.Geosets?.some((it) => it.SkinWeights?.length > 0);\n\t\t\tthis.shaderProgramLocations = {"""
        new = """\t\t\tthis.isHD = model.Geosets?.some((it) => it.SkinWeights?.length > 0);\n\t\t\t// Patch 3.0 can store bone ids as uint16. Do not inflate the GLSL\n\t\t\t// uniform array to 512 globally: many WebView2/WebGL drivers cannot link\n\t\t\t// such a shader. Build a compact palette containing only bones actually\n\t\t\t// referenced by weighted vertices, and remap skin ids into that palette.\n\t\t\tthis.skinNodePalette = [];\n\t\t\tthis.skinNodeRemap = /* @__PURE__ */ new Map();\n\t\t\tthis.renderSkinWeights = [];\n\t\t\tif (this.isHD) {\n\t\t\t\tconst used = /* @__PURE__ */ new Set();\n\t\t\t\tfor (const geoset of model.Geosets || []) {\n\t\t\t\t\tconst skin = geoset.SkinWeights;\n\t\t\t\t\tif (!skin) continue;\n\t\t\t\t\tfor (let i = 0; i + 7 < skin.length; i += 8) {\n\t\t\t\t\t\tfor (let k = 0; k < 4; ++k) if (skin[i + 4 + k] > 0) used.add(skin[i + k]);\n\t\t\t\t\t}\n\t\t\t\t}\n\t\t\t\tthis.skinNodePalette = [...used].sort((a, b) => a - b);\n\t\t\t\tif (!this.skinNodePalette.length) this.skinNodePalette = [0];\n\t\t\t\tthis.skinNodePalette.forEach((nodeId, slot) => this.skinNodeRemap.set(nodeId, slot));\n\t\t\t}\n\t\t\tthis.nodeCapacity = this.isHD ? Math.max(1, this.skinNodePalette.length) : MAX_NODES;\n\t\t\tthis.shaderProgramLocations = {"""
        text = _replace_once(text, old, new, "compact bone palette")

    old = """\t\tinitGL(glContext) {\n\t\t\tthis.gl = glContext;\n\t\t\tthis.softwareSkinning = this.gl.getParameter(this.gl.MAX_VERTEX_UNIFORM_VECTORS) < 4 * (MAX_NODES + 2);\n\t\t\tthis.anisotropicExt = this.gl.getExtension(\"EXT_texture_filter_anisotropic\") || this.gl.getExtension(\"MOZ_EXT_texture_filter_anisotropic\") || this.gl.getExtension(\"WEBKIT_EXT_texture_filter_anisotropic\");"""
    if old in text:
        new = """\t\tinitGL(glContext) {\n\t\t\tthis.gl = glContext;\n\t\t\tconst maxVertexUniformVectors = Number(this.gl.getParameter(this.gl.MAX_VERTEX_UNIFORM_VECTORS) || 0);\n\t\t\tconst safeMatrixCapacity = Math.max(1, Math.floor((maxVertexUniformVectors - 16) / 4));\n\t\t\tthis.softwareSkinning = !this.isHD && maxVertexUniformVectors < 4 * (MAX_NODES + 2);\n\t\t\tif (this.isHD && this.nodeCapacity > safeMatrixCapacity) {\n\t\t\t\tthrow new Error(`HD/DE model needs ${this.nodeCapacity} active bones, but this WebGL device supports about ${safeMatrixCapacity}.`);\n\t\t\t}\n\t\t\tthis.anisotropicExt = this.gl.getExtension(\"EXT_texture_filter_anisotropic\") || this.gl.getExtension(\"MOZ_EXT_texture_filter_anisotropic\") || this.gl.getExtension(\"WEBKIT_EXT_texture_filter_anisotropic\");"""
        text = text.replace(old, new, 1)

    old = """\t\tinitShaders() {\n\t\t\tif (this.shaderProgram) return;\n\t\t\tlet vertexShaderSource;\n\t\t\tif (this.isHD) vertexShaderSource = isWebGL2(this.gl) ? vertexShaderHDHardwareSkinningNew : vertexShaderHDHardwareSkinningOld;\n\t\t\telse if (this.softwareSkinning) vertexShaderSource = sdSoftwareSkinning_vs_default;\n\t\t\telse vertexShaderSource = vertexShaderHardwareSkinning;"""
    if old in text:
        new = """\t\tinitShaders() {\n\t\t\tif (this.shaderProgram) return;\n\t\t\tlet vertexShaderSource;\n\t\t\tif (this.isHD) {\n\t\t\t\tconst template = isWebGL2(this.gl) ? hdHardwareSkinningNew_vs_default : hdHardwareSkinningOld_vs_default;\n\t\t\t\tvertexShaderSource = template.replace(/\\$\\{MAX_NODES}/g, String(this.nodeCapacity));\n\t\t\t}\n\t\t\telse if (this.softwareSkinning) vertexShaderSource = sdSoftwareSkinning_vs_default;\n\t\t\telse vertexShaderSource = vertexShaderHardwareSkinning;"""
        text = text.replace(old, new, 1)

    old = """\t\t\tthis.gl.linkProgram(shaderProgram);\n\t\t\tif (!this.gl.getProgramParameter(shaderProgram, this.gl.LINK_STATUS)) alert(\"Could not initialise shaders\");\n\t\t\tthis.gl.useProgram(shaderProgram);"""
    if old in text:
        new = """\t\t\tthis.gl.linkProgram(shaderProgram);\n\t\t\tif (!this.gl.getProgramParameter(shaderProgram, this.gl.LINK_STATUS)) {\n\t\t\t\tconst info = this.gl.getProgramInfoLog(shaderProgram) || \"unknown shader link error\";\n\t\t\t\tthrow new Error(`WebGL shader link failed: ${info}`);\n\t\t\t}\n\t\t\tthis.gl.useProgram(shaderProgram);"""
        text = text.replace(old, new)


    old = """\t\t\tif (!this.softwareSkinning) {\n\t\t\t\tthis.shaderProgramLocations.nodesMatricesAttributes = [];\n\t\t\t\tfor (let i = 0; i < MAX_NODES; ++i) this.shaderProgramLocations.nodesMatricesAttributes[i] = this.gl.getUniformLocation(shaderProgram, `uNodesMatrices[${i}]`);\n\t\t\t}"""
    if old in text:
        new = """\t\t\tif (!this.softwareSkinning) {\n\t\t\t\tthis.shaderProgramLocations.nodesMatricesAttributes = [];\n\t\t\t\tconst matrixCount = this.isHD ? this.nodeCapacity : MAX_NODES;\n\t\t\t\tfor (let i = 0; i < matrixCount; ++i) this.shaderProgramLocations.nodesMatricesAttributes[i] = this.gl.getUniformLocation(shaderProgram, `uNodesMatrices[${i}]`);\n\t\t\t}"""
        text = text.replace(old, new, 1)

    old = """\t\t\tif (!this.softwareSkinning) {\n\t\t\t\tfor (let j = 0; j < MAX_NODES; ++j) if (this.rendererData.nodes[j]) this.gl.uniformMatrix4fv(this.shaderProgramLocations.nodesMatricesAttributes[j], false, this.rendererData.nodes[j].matrix);\n\t\t\t}"""
    if old in text:
        new = """\t\t\tif (!this.softwareSkinning) {\n\t\t\t\tif (this.isHD) {\n\t\t\t\t\tfor (let slot = 0; slot < this.nodeCapacity; ++slot) {\n\t\t\t\t\t\tconst node = this.rendererData.nodes[this.skinNodePalette[slot]];\n\t\t\t\t\t\tif (node) this.gl.uniformMatrix4fv(this.shaderProgramLocations.nodesMatricesAttributes[slot], false, node.matrix);\n\t\t\t\t\t}\n\t\t\t\t} else {\n\t\t\t\t\tfor (let j = 0; j < MAX_NODES; ++j) if (this.rendererData.nodes[j]) this.gl.uniformMatrix4fv(this.shaderProgramLocations.nodesMatricesAttributes[j], false, this.rendererData.nodes[j].matrix);\n\t\t\t\t}\n\t\t\t}"""
        text = text.replace(old, new, 1)

    old = """\t\t\t\tif (this.isHD) {\n\t\t\t\t\tthis.skinWeightBuffer[i] = this.gl.createBuffer();\n\t\t\t\t\tthis.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.skinWeightBuffer[i]);\n\t\t\t\t\tthis.gl.bufferData(this.gl.ARRAY_BUFFER, geoset.SkinWeights, this.gl.STATIC_DRAW);\n\t\t\t\t\tthis.tangentBuffer[i] = this.gl.createBuffer();"""
    if old in text:
        new = """\t\t\t\tif (this.isHD) {\n\t\t\t\t\tconst sourceSkin = geoset.SkinWeights || new Uint8Array(0);\n\t\t\t\t\tconst skin = sourceSkin instanceof Uint16Array ? new Uint16Array(sourceSkin) : new Uint8Array(sourceSkin);\n\t\t\t\t\tfor (let j = 0; j + 7 < skin.length; j += 8) {\n\t\t\t\t\t\tfor (let k = 0; k < 4; ++k) {\n\t\t\t\t\t\t\tconst mapped = this.skinNodeRemap.get(sourceSkin[j + k]);\n\t\t\t\t\t\t\tskin[j + k] = mapped === void 0 ? 0 : mapped;\n\t\t\t\t\t\t}\n\t\t\t\t\t}\n\t\t\t\t\tthis.renderSkinWeights[i] = skin;\n\t\t\t\t\tthis.skinWeightBuffer[i] = this.gl.createBuffer();\n\t\t\t\t\tthis.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.skinWeightBuffer[i]);\n\t\t\t\t\tthis.gl.bufferData(this.gl.ARRAY_BUFFER, skin, this.gl.STATIC_DRAW);\n\t\t\t\t\tthis.tangentBuffer[i] = this.gl.createBuffer();"""
        text = text.replace(old, new, 1)

    old = """\t\t\t\t\tthis.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.skinWeightBuffer[i]);\n\t\t\t\t\tconst wideSkin = geoset.SkinWeights instanceof Uint16Array;\n\t\t\t\t\tconst skinType = wideSkin ? this.gl.UNSIGNED_SHORT : this.gl.UNSIGNED_BYTE;"""
    if old in text:
        new = """\t\t\t\t\tthis.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.skinWeightBuffer[i]);\n\t\t\t\t\tconst renderSkin = this.renderSkinWeights[i] || geoset.SkinWeights;\n\t\t\t\t\tconst wideSkin = renderSkin instanceof Uint16Array;\n\t\t\t\t\tconst skinType = wideSkin ? this.gl.UNSIGNED_SHORT : this.gl.UNSIGNED_BYTE;"""
        text = text.replace(old, new, 1)

    required = [
        "SkinWeightsElementBytes = wideSkin ? 2 : 1",
        "this.skinNodePalette = [];",
        "this.nodeCapacity = this.isHD ? Math.max(1, this.skinNodePalette.length) : MAX_NODES",
        "WebGL shader compile failed",
        "const renderSkin = this.renderSkinWeights[i] || geoset.SkinWeights",
        "Some stock Reforged HD models report a camera inclusive size",
    ]
    missing = [m for m in required if m not in text]
    if missing:
        raise RuntimeError("falha ao aplicar compatibilidade DE/render v3.10.9: " + ", ".join(missing))

    dest.write_text(text, encoding="utf-8")
    try:
        shown = dest.relative_to(ROOT)
    except ValueError:
        shown = dest
    print(f"patch DE/render/CAMS aplicado: {shown}")


def main():
    VENDOR.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        dest = VENDOR / name
        if dest.exists() and dest.stat().st_size >= 10_000:
            print(f"já existe: {dest.relative_to(ROOT)}")
        else:
            fetch(url, dest)
        if name == "war3-model.browser.js":
            patch_war3_model_de(dest)
    print("Viewer pronto.")


if __name__ == "__main__":
    LOGGER.info("setup_viewer START")
    try:
        main()
        LOGGER.info("setup_viewer DONE")
    except BaseException as exc:
        log_exception(LOGGER, "setup_viewer", exc)
        raise
