import cv2

print("--- DIAGNÓSTICO DE CÁMARA ---")

# 1. Probar CAP_DSHOW con resolución forzada previa
print("\n[1] Probando DirectShow (CAP_DSHOW)...")
cap_dshow = cv2.VideoCapture(0, cv2.CAP_DSHOW)
if cap_dshow.isOpened():
    ret, frame = cap_dshow.read()
    print(f" -> DSHOW abierto: {ret}")
    if ret and frame is not None:
        print(f" -> Resolución capturada: {frame.shape[1]}x{frame.shape[0]}")
    cap_dshow.release()
else:
    print(" -> DSHOW no abrió.")

# 2. Probar índice 1 (por si el índice 0 es sensor IR de Windows Hello)
print("\n[2] Probando índice 1 con DirectShow...")
cap_1 = cv2.VideoCapture(1, cv2.CAP_DSHOW)
if cap_1.isOpened():
    ret, frame = cap_1.read()
    print(f" -> Índice 1 abierto: {ret}")
    cap_1.release()
else:
    print(" -> Índice 1 no disponible.")

print("\n--- FIN DIAGNÓSTICO ---")