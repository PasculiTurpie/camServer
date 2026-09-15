<div align="center">

# 📹 CamServer

### Transmisión de video local y procesamiento de cámaras en tiempo real con Python & OpenCV

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![OpenCV](https://img.shields.io/badge/OpenCV-Computer%20Vision-5C3EE8?style=for-the-badge&logo=opencv&logoColor=white)](https://opencv.org/)
[![License](https://img.shields.io/badge/License-MIT-00C853?style=for-the-badge)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Active-blue?style=for-the-badge)]()

<p align="center">
  <b>Servidor de captura, procesamiento y streaming de video de baja latencia diseñado para redes locales e integración de visión por computador.</b>
</p>

[Características](#-características) • [Instalación](#-instalación) • [Uso](#-uso) • [Estructura](#-estructura-del-proyecto) • [Contribución](#-contribución)

---

</div>

## ⚡ Características

* **Baja Latencia:** Streaming optimizado para redes de área local (LAN).
* **Compatibilidad Multi-Dispositivo:** Detección y lectura de cámaras USB nativas, virtuales y feeds RTSP/HTTP.
* **Procesamiento OpenCV:** Soporte nativo para manipulación de frames, filtros y detección de bordes en tiempo real.
* **Compilación Independiente:** Arquitectura lista para empaquetado autónomo en binarios `.exe` (PyInstaller).

---

## 🛠️ Tecnologías

| Herramienta | Rol |
| :--- | :--- |
| **Python** | Lenguaje principal de ejecución |
| **OpenCV (`cv2`)** | Captura y procesamiento matricial de cuadros de video |
| **NumPy** | Operaciones numéricas y buffers de memoria para frames |
| **PyInstaller** | Empaquetado a ejecutable nativo |

---

## 🚀 Instalación y Puesta en Marcha

### Prerrequisitos
* Python **3.10** o superior instalado
* Cámara web conectada o URL de stream RTSP

### 1. Clonar el repositorio
```bash
git clone [https://github.com/PasculiTurpie/camServer.git](https://github.com/PasculiTurpie/camServer.git)
cd camServer