import cv2

print("Probando backend MSMF en índice 0...")
cap = cv2.VideoCapture(0, cv2.CAP_MSMF)

if not cap.isOpened():
    print("MSMF falló. Probando backend directo...")
    cap = cv2.VideoCapture(0)

if cap.isOpened():
    ret, frame = cap.read()
    if ret and frame is not None:
        print("[EXITO] La cámara respondió y capturó un cuadro correctamente.")
    else:
        print("[ERROR] El dispositivo abrió pero no entregó fotogramas.")
    cap.release()
else:
    print("[ERROR] No se pudo abrir la cámara. Revisa permisos en Configuración > Privacidad de Windows.")