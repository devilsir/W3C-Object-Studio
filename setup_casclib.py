from wc3injector.casc import ensure_casclib, detect_warcraft_install

print("WC3 Object Studio — suporte CASC local")
try:
    p=ensure_casclib(timeout=30.0)
    print("CascLib:",p)
except Exception as exc:
    print("[AVISO] Falha ao preparar CascLib:",exc)
    raise SystemExit(2)
install=detect_warcraft_install()
if install:
    print("Warcraft III detectado:",install)
else:
    print("[AVISO] Warcraft III não foi detectado automaticamente.")
    print("Use o botão CASC… no Studio para escolher a pasta que contém .build.info/Data/_retail_.")
print("OK")
