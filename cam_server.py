import sys
import io
import os
import glob
import subprocess
import wave

if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

# Parche de metadatos para PyInstaller
import importlib.metadata
_orig_version = importlib.metadata.version
def _safe_version(pkg):
    if pkg == "imageio":
        return "2.34.0"
    try:
        return _orig_version(pkg)
    except Exception:
        return "1.0.0"
importlib.metadata.version = _safe_version

import cv2
import time
import threading
import struct
import numpy as np
import sounddevice as sd
import asyncio
import imageio
import imageio_ffmpeg
from datetime import datetime
from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
import uvicorn

STORAGE_DIR = os.path.join(os.getcwd(), "recordings")
os.makedirs(STORAGE_DIR, exist_ok=True)

app = FastAPI(title="Servidor Multi-Cámara con Cuadrícula y Gestos")

CAMERAS_CONFIG = {
    "local_0": {"name": "Webcam Notebook (Principal)", "source": 0, "is_local": True},
    "local_1": {"name": "Webcam Notebook (Secundaria)", "source": 1, "is_local": True},
    "ezviz_1": {
        "name": "Porton",
        "source": "rtsp://admin:YAZMJC@192.168.1.98:554/h264_stream",
        "is_local": False
    },
    "ezviz_2": {
        "name": "Segundo piso",
        "source": "rtsp://admin:NZVTAL@192.168.1.87:554/h264_stream",
        "is_local": False
    },
}

CAM_KEYS = list(CAMERAS_CONFIG.keys())
active_camera_single = CAM_KEYS[0]

is_system_enabled = True
is_audio_enabled = False
is_running = True

# Diccionarios de fotogramas compartidos
latest_jpegs = {}
latest_raw_frames = {}
frames_lock = threading.Lock()

# Control de Grabación AV
is_recording = False
recording_target_cam = active_camera_single
video_writer = None
recording_filename = None
temp_video_path = None
temp_audio_path = None
record_start_time = 0.0
recorded_frames_count = 0
recording_lock = threading.Lock()

AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_BLOCK_SIZE = 1024


def create_placeholder_jpeg(text="SIN SEÑAL"):
    frame = np.zeros((360, 480, 3), dtype=np.uint8)
    cv2.putText(
        frame, text, (40, 180),
        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (160, 160, 160), 2, cv2.LINE_AA
    )
    ret, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
    return buffer.tobytes() if ret else b""


# Inicializar placeholders
for k in CAM_KEYS:
    latest_jpegs[k] = create_placeholder_jpeg("CONECTANDO...")


def single_camera_worker(cam_key, info):
    """Hilo dedicado e independiente para cada cámara."""
    global latest_jpegs, latest_raw_frames, is_running, is_system_enabled
    global video_writer, is_recording, recorded_frames_count, recording_target_cam
    
    cap = None
    consecutive_failures = 0

    while is_running:
        if not is_system_enabled:
            if cap is not None:
                cap.release()
                cap = None
            with frames_lock:
                latest_jpegs[cam_key] = create_placeholder_jpeg("SISTEMA APAGADO")
                latest_raw_frames[cam_key] = None
            time.sleep(0.5)
            continue

        if cap is None:
            if info["is_local"]:
                cap = cv2.VideoCapture(info["source"], cv2.CAP_DSHOW)
            else:
                cap = cv2.VideoCapture(info["source"])
            consecutive_failures = 0

            if not cap.isOpened():
                with frames_lock:
                    latest_jpegs[cam_key] = create_placeholder_jpeg("NO DISPONIBLE")
                time.sleep(2.0)
                continue

        success, frame = cap.read()

        if not success or frame is None:
            consecutive_failures += 1
            if consecutive_failures >= 10:
                with frames_lock:
                    latest_jpegs[cam_key] = create_placeholder_jpeg("RECONECTANDO...")
                if cap is not None:
                    cap.release()
                    cap = None
                time.sleep(2.5)
                consecutive_failures = 0
            else:
                time.sleep(0.05)
            continue

        consecutive_failures = 0

        h, w = frame.shape[:2]
        if w > 1280:
            frame = cv2.resize(frame, (1280, int(h * 1280 / w)))

        # Grabar si es la cámara seleccionada para la sesión
        with recording_lock:
            if is_recording and recording_target_cam == cam_key and video_writer is not None:
                try:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    video_writer.append_data(frame_rgb)
                    recorded_frames_count += 1
                except Exception:
                    pass

        ret, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
        if ret:
            with frames_lock:
                latest_jpegs[cam_key] = buffer.tobytes()
                latest_raw_frames[cam_key] = frame.copy()

        time.sleep(0.02)

    if cap is not None:
        cap.release()


# Iniciar un hilo por cada cámara configurada
for k, info in CAMERAS_CONFIG.items():
    threading.Thread(target=single_camera_worker, args=(k, info), daemon=True).start()


def audio_recorder_worker(target_wav_path):
    try:
        wf = wave.open(target_wav_path, 'wb')
        wf.setnchannels(AUDIO_CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(AUDIO_SAMPLE_RATE)

        with sd.InputStream(
            samplerate=AUDIO_SAMPLE_RATE,
            channels=AUDIO_CHANNELS,
            dtype="int16",
            blocksize=AUDIO_BLOCK_SIZE
        ) as stream:
            while is_recording:
                data, _ = stream.read(AUDIO_BLOCK_SIZE)
                wf.writeframes(data.tobytes())

        wf.close()
    except Exception:
        pass


def audio_stream_generator():
    header = b"RIFF\xff\xff\xff\x7fWAVEfmt \x10\x00\x00\x00\x01\x00"
    header += struct.pack("<H", AUDIO_CHANNELS)
    header += struct.pack("<I", AUDIO_SAMPLE_RATE)
    header += struct.pack("<I", AUDIO_SAMPLE_RATE * AUDIO_CHANNELS * 2)
    header += struct.pack("<H", AUDIO_CHANNELS * 2)
    header += struct.pack("<H", 16)
    header += b"data\xff\xff\xff\x7f"
    yield header

    with sd.InputStream(
        samplerate=AUDIO_SAMPLE_RATE,
        channels=AUDIO_CHANNELS,
        dtype="int16",
        blocksize=AUDIO_BLOCK_SIZE,
    ) as stream:
        while is_running and is_audio_enabled:
            data, overflowed = stream.read(AUDIO_BLOCK_SIZE)
            yield data.tobytes()


@app.websocket("/ws/video/{cam_id}")
async def websocket_video_endpoint(websocket: WebSocket, cam_id: str):
    """Canal WebSocket multiplexado por ID de cámara."""
    await websocket.accept()
    try:
        while is_running:
            with frames_lock:
                frame_data = latest_jpegs.get(cam_id)

            if frame_data is not None:
                await websocket.send_bytes(frame_data)

            await asyncio.sleep(0.05)
    except (WebSocketDisconnect, Exception):
        pass


@app.get("/api/cameras_list")
def get_cameras_list():
    return {
        "cameras": [{"id": k, "name": v["name"]} for k, v in CAMERAS_CONFIG.items()],
        "current": active_camera_single
    }


@app.get("/api/toggle_system")
def toggle_system():
    global is_system_enabled
    is_system_enabled = not is_system_enabled
    return {"status": "ok", "enabled": is_system_enabled}


@app.get("/api/toggle_audio")
def toggle_audio():
    global is_audio_enabled
    is_audio_enabled = not is_audio_enabled
    return {"status": "ok", "audio_enabled": is_audio_enabled}


@app.get("/api/set_single_camera/{cam_key}")
def set_single_camera(cam_key: str):
    global active_camera_single
    if cam_key in CAMERAS_CONFIG:
        active_camera_single = cam_key
    return {"status": "ok", "active_camera": active_camera_single}


@app.get("/api/recording/status")
def get_recording_status():
    return {"is_recording": is_recording, "filename": recording_filename, "target": recording_target_cam}


@app.get("/api/recording/toggle")
def toggle_recording(target: str = None):
    global is_recording, video_writer, recording_filename, recording_target_cam
    global temp_video_path, temp_audio_path
    global record_start_time, recorded_frames_count

    with recording_lock:
        if not is_recording:
            cam_to_record = target if target in CAMERAS_CONFIG else active_camera_single
            with frames_lock:
                raw_frame = latest_raw_frames.get(cam_to_record)
                if raw_frame is None:
                    return {"status": "error", "message": "No hay señal en la cámara a grabar"}

            recording_target_cam = cam_to_record
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            recording_filename = f"video_{recording_target_cam}_{timestamp}.mp4"
            
            temp_video_path = os.path.join(STORAGE_DIR, f"temp_{timestamp}.mp4")
            temp_audio_path = os.path.join(STORAGE_DIR, f"temp_{timestamp}.wav")

            video_writer = imageio.get_writer(
                temp_video_path,
                fps=20,
                codec="libx264",
                format="FFMPEG",
                pixelformat="yuv420p"
            )
            is_recording = True
            recorded_frames_count = 0
            record_start_time = time.time()

            threading.Thread(target=audio_recorder_worker, args=(temp_audio_path,), daemon=True).start()

            return {"status": "ok", "recording": True, "file": recording_filename, "target": recording_target_cam}
        else:
            is_recording = False
            saved_file = recording_filename
            elapsed_time = max(0.1, time.time() - record_start_time)

            if video_writer is not None:
                video_writer.close()
                video_writer = None

            time.sleep(0.4)

            final_output_path = os.path.join(STORAGE_DIR, saved_file)
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            actual_fps = max(5.0, round(recorded_frames_count / elapsed_time, 2))

            if os.path.exists(temp_video_path) and os.path.exists(temp_audio_path):
                cmd = [
                    ffmpeg_exe, "-y",
                    "-r", str(actual_fps),
                    "-i", temp_video_path,
                    "-i", temp_audio_path,
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-shortest",
                    final_output_path
                ]
                subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )

                try:
                    os.remove(temp_video_path)
                    os.remove(temp_audio_path)
                except Exception:
                    pass
            elif os.path.exists(temp_video_path):
                os.rename(temp_video_path, final_output_path)

            recording_filename = None
            return {"status": "ok", "recording": False, "saved_file": saved_file}


@app.get("/api/snapshot")
def take_snapshot(target: str = None):
    cam_to_snap = target if target in CAMERAS_CONFIG else active_camera_single
    with frames_lock:
        frame = latest_raw_frames.get(cam_to_snap)
        if frame is None:
            return Response(content="Cámara inactiva", status_code=503)
        frame_copy = frame.copy()

    ret, buffer = cv2.imencode(".jpg", frame_copy, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    if not ret:
        return Response(content="Error", status_code=500)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"foto_{cam_to_snap}_{timestamp}.jpg"
    filepath = os.path.join(STORAGE_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(buffer.tobytes())

    return Response(
        content=buffer.tobytes(),
        media_type="image/jpeg",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/files")
def list_files():
    items = []
    files = glob.glob(os.path.join(STORAGE_DIR, "*.*"))
    files = [f for f in files if not os.path.basename(f).startswith("temp_")]
    files.sort(key=os.path.getmtime, reverse=True)

    for f in files:
        fname = os.path.basename(f)
        size_bytes = os.path.getsize(f)
        mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M:%S")
        is_video = fname.lower().endswith(".mp4")
        size_str = f"{size_bytes / (1024 * 1024):.1f} MB" if size_bytes > 1024 * 1024 else f"{size_bytes / 1024:.1f} KB"

        items.append({
            "name": fname,
            "size": size_str,
            "date": mtime,
            "type": "video" if is_video else "image"
        })
    return {"files": items}


@app.get("/api/files/download/{filename}")
def download_file(filename: str):
    filepath = os.path.join(STORAGE_DIR, os.path.basename(filename))
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(filepath, filename=filename)


@app.delete("/api/files/delete/{filename}")
def delete_file(filename: str):
    filepath = os.path.join(STORAGE_DIR, os.path.basename(filename))
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    os.remove(filepath)
    return {"status": "ok", "deleted": filename}


@app.get("/audio")
def audio_feed():
    if not is_audio_enabled:
        return Response(status_code=404)
    return StreamingResponse(audio_stream_generator(), media_type="audio/wav")


@app.get("/", response_class=HTMLResponse)
def index():
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
        <title>Panel Multi-Cámara</title>
        <style>
            body {
                background-color: #121212;
                color: #f1f1f1;
                font-family: system-ui, -apple-system, sans-serif;
                display: flex;
                flex-direction: column;
                align-items: center;
                margin: 0;
                padding: 10px;
                box-sizing: border-box;
                touch-action: pan-y;
            }
            .header-bar {
                display: flex;
                justify-content: space-between;
                width: 100%;
                max-width: 960px;
                align-items: center;
                margin-bottom: 8px;
            }
            .header-bar h2 { margin: 0; font-size: 1.2rem; }
            .controls {
                margin-bottom: 10px;
                display: flex;
                gap: 8px;
                align-items: center;
                flex-wrap: wrap;
                justify-content: center;
                max-width: 960px;
            }
            .btn {
                padding: 7px 11px;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 500;
                cursor: pointer;
                border: 1px solid #444;
            }
            .grid-switcher {
                display: flex;
                background: #222;
                border-radius: 6px;
                overflow: hidden;
                border: 1px solid #444;
            }
            .grid-btn {
                background: transparent;
                border: none;
                color: #ccc;
                padding: 7px 12px;
                cursor: pointer;
                font-weight: bold;
                font-size: 12px;
            }
            .grid-btn.active {
                background: #0284c7;
                color: white;
            }
            .btn-power-on { background-color: #ef4444; color: #fff; border: none; }
            .btn-power-off { background-color: #10b981; color: #fff; border: none; }
            .btn-audio-off { background-color: #4b5563; color: #fff; border: none; }
            .btn-audio-on { background-color: #8b5cf6; color: #fff; border: none; }
            .btn-record-off { background-color: #dc2626; color: #fff; border: none; }
            .btn-record-on { background-color: #991b1b; color: #fff; border: 2px solid #f87171; animation: pulse 1.5s infinite; }
            .btn-snapshot { background-color: #0284c7; color: #fff; border: none; }
            .btn-files { background-color: #374151; color: #fff; border: 1px solid #6b7280; }
            .btn:active { transform: scale(0.97); }
            @keyframes pulse {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.6; }
            }

            /* Contenedores de Cuadrícula */
            #videoViewport {
                position: relative;
                width: 100%;
                max-width: 960px;
                background: #000;
                border-radius: 8px;
                overflow: hidden;
                border: 1px solid #333;
                user-select: none;
            }
            .grid-container {
                display: grid;
                width: 100%;
                gap: 4px;
                background: #000;
            }
            .grid-1 { grid-template-columns: 1fr; }
            .grid-2 { grid-template-columns: 1fr; }
            @media (min-width: 600px) {
                .grid-2 { grid-template-columns: 1fr 1fr; }
            }
            .grid-4 { grid-template-columns: 1fr 1fr; }

            .cam-card {
                position: relative;
                width: 100%;
                background: #000;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                aspect-ratio: 4 / 3;
            }
            .cam-card canvas {
                width: 100%;
                height: 100%;
                object-fit: contain;
                display: block;
            }
            .cam-badge {
                position: absolute;
                top: 8px;
                left: 8px;
                background: rgba(0,0,0,0.6);
                padding: 4px 8px;
                border-radius: 4px;
                font-size: 11px;
                color: #fff;
                pointer-events: none;
            }

            #swipeHint {
                font-size: 11px;
                color: #777;
                margin-top: 5px;
            }

            /* Modal Gestor */
            .modal {
                display: none;
                position: fixed;
                z-index: 100;
                left: 0; top: 0;
                width: 100%; height: 100%;
                background-color: rgba(0,0,0,0.85);
                justify-content: center;
                align-items: center;
            }
            .modal-content {
                background-color: #1e1e1e;
                padding: 16px;
                border-radius: 8px;
                width: 92%;
                max-width: 750px;
                max-height: 80vh;
                overflow-y: auto;
                box-sizing: border-box;
            }
            .modal-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 1px solid #333;
                padding-bottom: 8px;
                margin-bottom: 12px;
            }
            .close-btn { background: none; border: none; color: #aaa; font-size: 24px; cursor: pointer; }
            .file-table { width: 100%; border-collapse: collapse; font-size: 12px; }
            .file-table th, .file-table td { padding: 8px; text-align: left; border-bottom: 1px solid #2a2a2a; }
            .file-table th { color: #9ca3af; }
            .btn-action { padding: 4px 8px; font-size: 11px; border-radius: 4px; cursor: pointer; border: none; }
            .btn-dl { background: #0284c7; color: white; margin-right: 4px; }
            .btn-del { background: #ef4444; color: white; }
        </style>
    </head>
    <body>
        <div class="header-bar">
            <h2>Panel Multi-Cámara</h2>
            <div class="grid-switcher">
                <button class="grid-btn active" onclick="setGridMode(1)">1 Cam</button>
                <button class="grid-btn" onclick="setGridMode(2)">2 Cams</button>
                <button class="grid-btn" onclick="setGridMode(4)">4 Cams</button>
            </div>
        </div>

        <div class="controls">
            <button id="btnPower" class="btn btn-power-on" onclick="toggleSystem()">⏻ Apagar</button>
            <button id="btnAudio" class="btn btn-audio-off" onclick="toggleAudio()">🔇 Audio</button>
            <button id="btnRecord" class="btn btn-record-off" onclick="toggleRecord()">🔴 Grabar Video</button>
            <button class="btn btn-snapshot" onclick="downloadSnapshot()">📷 Foto</button>
            <button class="btn btn-files" onclick="openFileManager()">📁 Ver Archivos</button>
        </div>

        <!-- Visor táctil interactivo -->
        <div id="videoViewport">
            <div id="gridContainer" class="grid-container grid-1"></div>
        </div>
        <div id="swipeHint">👉 Desliza con el dedo horizontalmente para cambiar de cámara</div>

        <audio id="audioPlayer" preload="none"></audio>

        <!-- Modal Gestor de Archivos -->
        <div id="fileModal" class="modal">
            <div class="modal-content">
                <div class="modal-header">
                    <h3 style="margin:0;">Grabaciones y Fotos Guardadas</h3>
                    <button class="close-btn" onclick="closeFileManager()">&times;</button>
                </div>
                <table class="file-table">
                    <thead>
                        <tr>
                            <th>Nombre</th>
                            <th>Fecha</th>
                            <th>Tamaño</th>
                            <th>Acciones</th>
                        </tr>
                    </thead>
                    <tbody id="filesListBody"></tbody>
                </table>
            </div>
        </div>

        <script>
            let cameras = [];
            let currentSingleIdx = 0;
            let currentGridMode = 1;
            let activeSockets = {};
            let isSystemEnabled = true;
            let isAudioEnabled = false;
            let isRecording = false;

            const gridContainer = document.getElementById('gridContainer');
            const videoViewport = document.getElementById('videoViewport');
            const audioPlayer = document.getElementById('audioPlayer');
            const swipeHint = document.getElementById('swipeHint');

            async function init() {
                const res = await fetch('/api/cameras_list');
                const data = await res.json();
                cameras = data.cameras;
                renderGrid();
                setupSwipeEvents();
            }

            function setGridMode(mode) {
                currentGridMode = mode;
                document.querySelectorAll('.grid-btn').forEach((b, i) => {
                    b.classList.toggle('active', [1, 2, 4][i] === mode);
                });
                swipeHint.style.display = (mode === 1) ? 'block' : 'none';
                renderGrid();
            }

            function closeSockets() {
                Object.values(activeSockets).forEach(ws => {
                    try { ws.close(); } catch(e) {}
                });
                activeSockets = {};
            }

            function renderGrid() {
                closeSockets();
                gridContainer.className = 'grid-container grid-' + currentGridMode;
                gridContainer.innerHTML = '';

                let camsToShow = [];
                if (currentGridMode === 1) {
                    camsToShow = [cameras[currentSingleIdx]];
                } else if (currentGridMode === 2) {
                    camsToShow = cameras.slice(0, 2);
                } else if (currentGridMode === 4) {
                    camsToShow = cameras.slice(0, 4);
                }

                camsToShow.forEach(cam => {
                    if (!cam) return;
                    const card = document.createElement('div');
                    card.className = 'cam-card';
                    card.innerHTML = `
                        <div class="cam-badge">${cam.name}</div>
                        <canvas id="canvas_${cam.id}" width="480" height="360"></canvas>
                    `;
                    gridContainer.appendChild(card);
                    initCamSocket(cam.id);
                });
            }

            function initCamSocket(camId) {
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const ws = new WebSocket(`${protocol}//${window.location.host}/ws/video/${camId}`);
                ws.binaryType = 'blob';
                const canvas = document.getElementById(`canvas_${camId}`);
                const ctx = canvas.getContext('2d');

                ws.onmessage = (event) => {
                    if (document.visibilityState !== 'visible') return;
                    createImageBitmap(event.data).then(bitmap => {
                        if (canvas.width !== bitmap.width) {
                            canvas.width = bitmap.width;
                            canvas.height = bitmap.height;
                        }
                        ctx.drawImage(bitmap, 0, 0);
                        bitmap.close();
                    }).catch(() => {});
                };

                ws.onclose = () => {
                    if (activeSockets[camId]) {
                        setTimeout(() => initCamSocket(camId), 1500);
                    }
                };

                activeSockets[camId] = ws;
            }

            // GESTOS TÁCTILES: SWIPE HORIZONTAL PARA MODO 1 CÁMARA
            function setupSwipeEvents() {
                let startX = 0;
                let startY = 0;

                videoViewport.addEventListener('touchstart', (e) => {
                    startX = e.touches[0].clientX;
                    startY = e.touches[0].clientY;
                }, { passive: true });

                videoViewport.addEventListener('touchend', (e) => {
                    if (currentGridMode !== 1 || cameras.length <= 1) return;

                    const diffX = e.changedTouches[0].clientX - startX;
                    const diffY = e.changedTouches[0].clientY - startY;

                    // Detectar si el gesto fue marcadamente horizontal
                    if (Math.abs(diffX) > 45 && Math.abs(diffX) > Math.abs(diffY)) {
                        if (diffX < 0) {
                            // Deslizar izquierda -> siguiente cámara
                            currentSingleIdx = (currentSingleIdx + 1) % cameras.length;
                        } else {
                            // Deslizar derecha -> cámara anterior
                            currentSingleIdx = (currentSingleIdx - 1 + cameras.length) % cameras.length;
                        }
                        fetch('/api/set_single_camera/' + cameras[currentSingleIdx].id);
                        renderGrid();
                    }
                }, { passive: true });
            }

            // CONTROLES DEL SISTEMA
            async function toggleSystem() {
                const res = await fetch('/api/toggle_system');
                const data = await res.json();
                isSystemEnabled = data.enabled;
                const btn = document.getElementById('btnPower');
                if (isSystemEnabled) {
                    btn.textContent = '⏻ Apagar';
                    btn.className = 'btn btn-power-on';
                    renderGrid();
                } else {
                    btn.textContent = '⏻ Encender';
                    btn.className = 'btn btn-power-off';
                }
            }

            async function toggleAudio() {
                const res = await fetch('/api/toggle_audio');
                const data = await res.json();
                isAudioEnabled = data.audio_enabled;
                const btn = document.getElementById('btnAudio');
                if (isAudioEnabled) {
                    btn.textContent = '🔊 Silenciar';
                    btn.className = 'btn btn-audio-on';
                    audioPlayer.src = '/audio?t=' + Date.now();
                    audioPlayer.play().catch(e => console.warn(e));
                } else {
                    btn.textContent = '🔇 Audio';
                    btn.className = 'btn btn-audio-off';
                    audioPlayer.pause();
                    audioPlayer.src = '';
                }
            }

            async function toggleRecord() {
                const target = cameras[currentSingleIdx].id;
                const res = await fetch('/api/recording/toggle?target=' + target);
                const data = await res.json();
                if (data.status === 'ok') {
                    isRecording = data.recording;
                    const btn = document.getElementById('btnRecord');
                    if (isRecording) {
                        btn.textContent = '⏹ Detener Grabación';
                        btn.className = 'btn btn-record-on';
                    } else {
                        btn.textContent = '🔴 Grabar Video';
                        btn.className = 'btn btn-record-off';
                    }
                } else {
                    alert(data.message || 'Error');
                }
            }

            function downloadSnapshot() {
                const target = cameras[currentSingleIdx].id;
                window.location.href = '/api/snapshot?target=' + target + '&t=' + Date.now();
            }

            // GESTOR DE ARCHIVOS
            async function openFileManager() {
                document.getElementById('fileModal').style.display = 'flex';
                await loadFilesList();
            }

            function closeFileManager() {
                document.getElementById('fileModal').style.display = 'none';
            }

            async function loadFilesList() {
                const tbody = document.getElementById('filesListBody');
                tbody.innerHTML = '<tr><td colspan="4">Cargando...</td></tr>';
                try {
                    const res = await fetch('/api/files');
                    const data = await res.json();
                    if (data.files.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="4">No hay grabaciones aún.</td></tr>';
                        return;
                    }
                    tbody.innerHTML = '';
                    data.files.forEach(file => {
                        const tr = document.createElement('tr');
                        const icon = file.type === 'video' ? '🎬' : '📷';
                        tr.innerHTML = `
                            <td>${icon} ${file.name}</td>
                            <td>${file.date}</td>
                            <td>${file.size}</td>
                            <td>
                                <button class="btn-action btn-dl" onclick="window.open('/api/files/download/${file.name}')">Descargar</button>
                                <button class="btn-action btn-del" onclick="deleteFile('${file.name}')">Eliminar</button>
                            </td>
                        `;
                        tbody.appendChild(tr);
                    });
                } catch (e) {
                    tbody.innerHTML = '<tr><td colspan="4">Error al obtener archivos.</td></tr>';
                }
            }

            async function deleteFile(filename) {
                if (!confirm(`¿Eliminar ${filename}?`)) return;
                await fetch('/api/files/delete/' + filename, { method: 'DELETE' });
                await loadFilesList();
            }

            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'visible') {
                    renderGrid();
                    if (isAudioEnabled) {
                        audioPlayer.pause();
                        audioPlayer.src = '/audio?t=' + Date.now();
                        audioPlayer.play().catch(() => {});
                    }
                }
            });

            init();
        </script>
    </body>
    </html>
    """


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None)