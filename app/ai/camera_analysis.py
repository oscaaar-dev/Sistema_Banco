import cv2
import numpy as np

FACE_CASCADE = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
EYE_CASCADE = cv2.data.haarcascades + 'haarcascade_eye_tree_eyeglasses.xml'

_face = cv2.CascadeClassifier(FACE_CASCADE)
_eyes = cv2.CascadeClassifier(EYE_CASCADE)


def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, float(value)))


def analyze_frame(frame):
    """Analiza una imagen de cámara sin guardar la imagen.

    La salida es una validación de calidad/visibilidad de la sesión, no una
    identificación biométrica ni un diagnóstico de emociones.
    """
    if frame is None or frame.size == 0:
        return {'ready': False, 'blocked': True, 'message': 'No se recibió una imagen válida de la cámara.'}

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    height, width = gray.shape[:2]

    faces = _face.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(80, 80))
    face_count = len(faces)

    if face_count == 0:
        return {
            'ready': False, 'blocked': True, 'face_detected': False,
            'face_count': 0, 'face_confidence': 0,
            'position_score': 0, 'visibility_score': 0,
            'possible_obstruction': True,
            'indicators': ['No se pudo localizar un rostro con suficiente claridad.']
        }

    if face_count > 1:
        return {
            'ready': False, 'blocked': True, 'face_detected': True,
            'face_count': face_count, 'face_confidence': 55,
            'position_score': 0, 'visibility_score': 0,
            'possible_obstruction': False,
            'indicators': ['Debe aparecer una sola persona frente a la cámara.']
        }

    x, y, w, h = faces[0]
    cx, cy = x + w / 2, y + h / 2
    center_error = ((cx - width / 2) / (width / 2)) ** 2 + ((cy - height / 2) / (height / 2)) ** 2
    position_score = _clamp(100 - np.sqrt(center_error) * 100)

    area_ratio = (w * h) / float(width * height)
    size_score = _clamp((area_ratio - 0.025) / 0.18 * 100)

    face_roi = gray[y:y+h, x:x+w]
    upper_roi = face_roi[:max(1, int(h * 0.65)), :]
    eyes = _eyes.detectMultiScale(upper_roi, scaleFactor=1.1, minNeighbors=5, minSize=(18, 18))
    eyes_visible = len(eyes) >= 2

    brightness = float(np.mean(face_roi))
    contrast = float(np.std(face_roi))
    lighting_score = _clamp(100 - abs(brightness - 125) * 0.75 + min(contrast, 45) * 0.35)

    # Heurística conservadora: ausencia de ambos ojos + zona ocular muy oscura
    # se marca como posible obstrucción, no como una identificación de objeto.
    eye_band = face_roi[int(h * 0.22):int(h * 0.52), :]
    dark_ratio = float(np.mean(eye_band < 55)) if eye_band.size else 0
    possible_dark_glasses = (not eyes_visible and dark_ratio > 0.28)
    possible_occlusion = (not eyes_visible) or possible_dark_glasses

    visibility_score = _clamp(
        (60 if eyes_visible else 25) +
        min(size_score * 0.25, 25) +
        min(lighting_score * 0.15, 15)
    )

    indicators = []
    if position_score < 60:
        indicators.append('Rostro fuera de la posición recomendada; céntrate frente a la cámara.')
    if size_score < 35:
        indicators.append('Acércate un poco a la cámara para mejorar la detección.')
    if not eyes_visible:
        indicators.append('No se distinguen correctamente los ojos; puede existir una obstrucción del rostro.')
    if possible_dark_glasses:
        indicators.append('Posible elemento oscuro cubriendo la zona de los ojos.')
    if brightness < 55:
        indicators.append('Hay poca iluminación en el rostro.')
    if brightness > 205:
        indicators.append('Hay demasiada iluminación directa en el rostro.')

    ready = (
        position_score >= 60 and
        size_score >= 35 and
        visibility_score >= 60 and
        eyes_visible and
        not possible_dark_glasses
    )

    if ready:
        message = 'Rostro visible y correctamente posicionado. Verificación de cámara disponible.'
    else:
        message = 'La operación permanece bloqueada hasta mejorar la visibilidad del rostro.'

    face_confidence = _clamp(50 + position_score * 0.2 + size_score * 0.15 + visibility_score * 0.15)
    return {
        'ready': bool(ready),
        'blocked': not bool(ready),
        'face_detected': True,
        'face_count': face_count,
        'face_confidence': round(face_confidence, 1),
        'position_score': round(position_score, 1),
        'visibility_score': round(visibility_score, 1),
        'eyes_visible': eyes_visible,
        'possible_obstruction': bool(possible_occlusion),
        'possible_dark_glasses': bool(possible_dark_glasses),
        'indicators': indicators or ['No se detectaron problemas de visibilidad.'],
        'message': message,
    }
