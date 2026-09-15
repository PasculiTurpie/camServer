import pygame.camera
import pygame.image

print("Inicializando cámara vía DirectShow/VFW...")
pygame.camera.init()
camlist = pygame.camera.list_cameras()
print(f"Cámaras encontradas: {camlist}")

if camlist:
    cam = pygame.camera.Camera(camlist[0], (640, 480))
    cam.start()
    print("Capturando fotograma de prueba...")
    image = cam.get_image()
    pygame.image.save(image, "foto_prueba.jpg")
    cam.stop()
    print("[EXITO] Foto guardada como foto_prueba.jpg")
else:
    print("[AVISO] No se detectó ninguna cámara registrada.")