from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import os
import threading
import time
import uuid
import tempfile
import logging
from werkzeug.utils import secure_filename

# MoviePy import (editor 없이)
from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy.audio.io.AudioFileClip import AudioFileClip
from moviepy.video.VideoClip import ImageClip, TextClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
from moviepy import concatenate_videoclips, concatenate_audioclips

# MoviePy 설정 - 2.x.x 호환 with 안전한 FFmpeg 설정
import moviepy.config as config

# FFmpeg 바이너리 안전하게 설정
try:
    # FFmpeg 경로 확인 및 설정
    import shutil
    ffmpeg_path = shutil.which('ffmpeg')
    if ffmpeg_path:
        config.FFMPEG_BINARY = ffmpeg_path
        print(f"✅ FFmpeg found at: {ffmpeg_path}")
    else:
        config.FFMPEG_BINARY = 'ffmpeg'  # 기본값 사용
        print("⚠️ FFmpeg not found in PATH, using default")
except Exception as e:
    config.FFMPEG_BINARY = 'ffmpeg'
    print(f"⚠️ FFmpeg 설정 중 오류: {e}")

# 임시 파일 경로를 절대 경로로 설정
temp_dir = os.path.abspath('temp')
os.makedirs(temp_dir, exist_ok=True)
config.TEMP_FOLDER = temp_dir

# MoviePy에서 subprocess 관련 오류 방지를 위한 추가 설정
try:
    # MoviePy의 내부 설정 조정
    import moviepy.video.io.ffmpeg_tools as ffmpeg_tools
    # FFmpeg 실행 시 stdout/stderr 처리 개선
    original_ffmpeg_parse_infos = getattr(ffmpeg_tools, 'ffmpeg_parse_infos', None)
    if original_ffmpeg_parse_infos:
        def safe_ffmpeg_parse_infos(filename, print_infos=False, check_duration=True):
            try:
                return original_ffmpeg_parse_infos(filename, print_infos=False, check_duration=check_duration)
            except Exception as e:
                print(f"Warning: FFmpeg info parsing failed: {e}")
                return None
        ffmpeg_tools.ffmpeg_parse_infos = safe_ffmpeg_parse_infos
    
    # subprocess.Popen 관련 오류 방지
    import subprocess
    original_popen = subprocess.Popen
    def safe_popen(*args, **kwargs):
        # stdout/stderr를 안전하게 처리
        if 'stdout' not in kwargs:
            kwargs['stdout'] = subprocess.PIPE
        if 'stderr' not in kwargs:
            kwargs['stderr'] = subprocess.PIPE
        try:
            return original_popen(*args, **kwargs)
        except Exception as e:
            print(f"Warning: subprocess.Popen failed: {e}")
            # DEVNULL로 재시도
            kwargs['stdout'] = subprocess.DEVNULL
            kwargs['stderr'] = subprocess.DEVNULL
            return original_popen(*args, **kwargs)
    
    # 원래 Popen을 안전한 버전으로 교체
    subprocess.Popen = safe_popen
    
except ImportError:
    pass  # 해당 모듈이 없으면 무시

# MoviePy 로깅 완전 비활성화
logging.getLogger('moviepy').setLevel(logging.ERROR)
logging.getLogger('imageio').setLevel(logging.ERROR)
logging.getLogger('imageio_ffmpeg').setLevel(logging.ERROR)

# subprocess stdout/stderr 오류 방지 데코레이터
def handle_subprocess_errors(func):
    """subprocess stdout/stderr 관련 오류를 처리하는 데코레이터"""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except AttributeError as e:
            if "'NoneType' object has no attribute 'stdout'" in str(e):
                print(f"⚠️ FFmpeg subprocess 오류 감지: {e}")
                print("🔄 대안 방법으로 재시도 중...")
                # 환경 변수로 FFmpeg 출력 제어
                import os
                old_env = os.environ.get('MOVIEPY_VERBOSE', None)
                os.environ['MOVIEPY_VERBOSE'] = 'False'
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e2:
                    print(f"❌ 재시도도 실패: {e2}")
                    raise e2
                finally:
                    if old_env is not None:
                        os.environ['MOVIEPY_VERBOSE'] = old_env
                    else:
                        os.environ.pop('MOVIEPY_VERBOSE', None)
            else:
                raise e
        except Exception as e:
            if "stdout" in str(e) or "stderr" in str(e):
                print(f"⚠️ 출력 스트림 관련 오류: {e}")
                # 최소한의 복구 시도
                try:
                    import sys
                    sys.stdout.flush()
                    sys.stderr.flush()
                except:
                    pass
            raise e
    return wrapper

# CompositeAudioClip import 시도
try:
    from moviepy.audio.AudioClip import CompositeAudioClip
except ImportError:
    try:
        from moviepy import CompositeAudioClip
    except ImportError:
        CompositeAudioClip = None

# volumex import - MoviePy 2.x에 맞는 방법
volumex = None
try:
    # MoviePy 2.x에서는 MultiplyVolume 사용
    from moviepy.audio.fx import MultiplyVolume
    # volumex 호환 함수 생성 - 올바른 방법
    def volumex(clip, factor):
        return clip.with_effects([MultiplyVolume(factor)])
except ImportError:
    volumex = None

# 안전한 TextClip 생성 함수
def create_text_clip_safe(text, font_size=50, color='white', stroke_color='black', stroke_width=2):
    """폰트 오류에 안전한 TextClip 생성 함수"""
    try:
        # 첫 번째 시도: stroke 효과와 함께
        return TextClip(
            text,
            font_size=font_size,
            color=color,
            stroke_color=stroke_color,
            stroke_width=stroke_width
        )
    except Exception as e1:
        print(f"Warning: stroke 효과로 자막 생성 실패: {e1}")
        try:
            # 두 번째 시도: stroke 없이
            return TextClip(
                text,
                font_size=font_size,
                color=color
            )
        except Exception as e2:
            print(f"Warning: 기본 설정으로 자막 생성 실패: {e2}")
            try:
                # 세 번째 시도: 최소한의 설정
                return TextClip(text, font_size=font_size)
            except Exception as e3:
                print(f"Error: 자막 생성 완전 실패: {e3}")
                # 마지막 시도: 매우 기본적인 설정
                return TextClip(text)

# 안전한 VideoFileClip 로딩 함수
@handle_subprocess_errors
def safe_load_video(filepath):
    """안전한 비디오 파일 로딩"""
    try:
        return VideoFileClip(filepath)
    except Exception as e:
        print(f"Warning: 비디오 로딩 실패: {e}")
        # 오디오 없이 로딩 시도
        try:
            return VideoFileClip(filepath, audio=False)
        except Exception as e2:
            print(f"Error: 오디오 없이도 로딩 실패: {e2}")
            raise e2

# 안전한 AudioFileClip 로딩 함수
@handle_subprocess_errors
def safe_load_audio(filepath):
    """안전한 오디오 파일 로딩"""
    try:
        return AudioFileClip(filepath)
    except Exception as e:
        print(f"Error: 오디오 로딩 실패: {e}")
        raise e

# 안전한 비디오 저장 함수
@handle_subprocess_errors
def safe_write_videofile(clip, output_path, **kwargs):
    """안전한 비디오 저장 함수 - stdout/stderr 오류 방지"""
    # MoviePy 2.x.x: 임시 오디오 파일 경로를 temp 폴더로 강제 지정
    if 'temp_audiofile' in kwargs:
        temp_filename = kwargs['temp_audiofile']
        if not os.path.isabs(temp_filename) and not temp_filename.startswith('temp'):
            kwargs['temp_audiofile'] = os.path.join(config.TEMP_FOLDER, os.path.basename(temp_filename))

    # MoviePy 2.x.x에서 stdout/stderr 오류 방지
    default_kwargs = {
        'codec': 'libx264',
        'audio_codec': 'aac',
        'logger': None,    # 로거 비활성화
        'write_logfile': False  # 로그 파일 쓰기 비활성화
    }
    
    # 기본값과 사용자 제공 kwargs 병합
    final_kwargs = {**default_kwargs, **kwargs}
    
    try:
        # stdout/stderr 리다이렉션으로 오류 방지
        import sys
        from io import StringIO
        
        # 표준 출력/에러 임시 저장
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        
        # 임시로 출력을 문자열 버퍼로 리다이렉션
        sys.stdout = StringIO()
        sys.stderr = StringIO()
        
        try:
            clip.write_videofile(output_path, **final_kwargs)
        finally:
            # 표준 출력/에러 복원
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            
    except Exception as e:
        print(f"Error: 비디오 저장 실패: {e}")
        # 대안 방법으로 재시도
        try:
            print("기본 설정으로 재시도 중...")
            # 최소한의 설정으로 재시도
            clip.write_videofile(
                output_path,
                codec='libx264',
                audio_codec='aac'
            )
        except Exception as e2:
            print(f"Error: 재시도도 실패: {e2}")
            raise e2

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# 작업 상태 관리
class TaskManager:
    def __init__(self):
        self.tasks = {}
        self.lock = threading.Lock()
    
    def create_task(self, task_id, task_type, total_steps=100):
        with self.lock:
            self.tasks[task_id] = {
                'id': task_id,
                'type': task_type,
                'status': 'running',  # running, paused, completed, cancelled, error
                'progress': 0,
                'total_steps': total_steps,
                'current_step': 0,
                'start_time': time.time(),
                'estimated_time': None,
                'message': '작업을 시작합니다...',
                'cancel_flag': threading.Event(),
                'pause_flag': threading.Event()
            }
        return self.tasks[task_id]
    
    def update_progress(self, task_id, current_step, message=""):
        with self.lock:
            if task_id in self.tasks:
                task = self.tasks[task_id]
                task['current_step'] = current_step
                task['progress'] = int((current_step / task['total_steps']) * 100)
                if message:
                    task['message'] = message
                
                # 남은 시간 계산
                elapsed_time = time.time() - task['start_time']
                if current_step > 0:
                    time_per_step = elapsed_time / current_step
                    remaining_steps = task['total_steps'] - current_step
                    task['estimated_time'] = remaining_steps * time_per_step
                
                # 클라이언트에게 진행상황 전송
                socketio.emit('task_progress', {
                    'task_id': task_id,
                    'progress': task['progress'],
                    'current_step': current_step,
                    'total_steps': task['total_steps'],
                    'message': task['message'],
                    'estimated_time': task['estimated_time'],
                    'status': task['status']
                })
    
    def set_status(self, task_id, status, message=""):
        with self.lock:
            if task_id in self.tasks:
                self.tasks[task_id]['status'] = status
                if message:
                    self.tasks[task_id]['message'] = message
                socketio.emit('task_status', {
                    'task_id': task_id,
                    'status': status,
                    'message': message
                })
    
    def cancel_task(self, task_id):
        with self.lock:
            if task_id in self.tasks:
                self.tasks[task_id]['cancel_flag'].set()
                self.set_status(task_id, 'cancelled', '작업이 취소되었습니다.')
    
    def pause_task(self, task_id):
        with self.lock:
            if task_id in self.tasks:
                self.tasks[task_id]['pause_flag'].set()
                self.set_status(task_id, 'paused', '작업이 일시정지되었습니다.')
    
    def resume_task(self, task_id):
        with self.lock:
            if task_id in self.tasks:
                self.tasks[task_id]['pause_flag'].clear()
                self.set_status(task_id, 'running', '작업을 재개합니다.')
    
    def is_cancelled(self, task_id):
        return task_id in self.tasks and self.tasks[task_id]['cancel_flag'].is_set()
    
    def is_paused(self, task_id):
        return task_id in self.tasks and self.tasks[task_id]['pause_flag'].is_set()
    
    def wait_if_paused(self, task_id):
        if task_id in self.tasks:
            while self.is_paused(task_id) and not self.is_cancelled(task_id):
                time.sleep(0.1)

task_manager = TaskManager()

# 설정
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
TEMP_FOLDER = 'temp'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['TEMP_FOLDER'] = TEMP_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB 제한

# 폴더 생성
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(TEMP_FOLDER, exist_ok=True)

# 시작 시 temp 파일 정리 함수
def cleanup_temp_files():
    """서버 시작 시 temp 파일들을 정리"""
    try:
        # temp 폴더의 모든 파일 삭제
        for filename in os.listdir(TEMP_FOLDER):
            filepath = os.path.join(TEMP_FOLDER, filename)
            if os.path.isfile(filepath):
                os.remove(filepath)
                print(f"Cleaned up temp file: {filename}")
        
        # 프로젝트 루트의 임시 파일들도 정리
        for filename in os.listdir('.'):
            if (filename.startswith('temp-audio') or 
                filename.startswith('My_Video') or 
                filename.endswith('.m4a')):
                try:
                    os.remove(filename)
                    print(f"Cleaned up root temp file: {filename}")
                except:
                    pass
    except Exception as e:
        print(f"Error during temp file cleanup: {e}")

# 서버 시작 시 temp 파일 정리 실행
cleanup_temp_files()

# 허용된 파일 확장자
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'webm'}
ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'bmp'}
ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'wav', 'aac', 'm4a', 'ogg'}

def allowed_file(filename, extensions):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in extensions

# SocketIO 이벤트 핸들러
@socketio.on('connect')
def handle_connect():
    print(f'클라이언트가 연결되었습니다: {request.sid}')
    emit('connected', {'data': '서버에 연결되었습니다'})

@socketio.on('disconnect')
def handle_disconnect():
    print(f'클라이언트가 연결을 해제했습니다: {request.sid}')

@socketio.on('cancel_task')
def handle_cancel_task(data):
    task_id = data.get('task_id')
    if task_id:
        task_manager.cancel_task(task_id)
        print(f'작업 취소 요청: {task_id}')

@socketio.on('pause_task')
def handle_pause_task(data):
    task_id = data.get('task_id')
    if task_id:
        task_manager.pause_task(task_id)
        print(f'작업 일시정지 요청: {task_id}')

@socketio.on('resume_task')
def handle_resume_task(data):
    task_id = data.get('task_id')
    if task_id:
        task_manager.resume_task(task_id)
        print(f'작업 재개 요청: {task_id}')

@socketio.on('get_task_status')
def handle_get_task_status(data):
    task_id = data.get('task_id')
    if task_id and task_id in task_manager.tasks:
        task = task_manager.tasks[task_id]
        emit('task_status', {
            'task_id': task_id,
            'status': task['status'],
            'progress': task['progress'],
            'message': task['message'],
            'estimated_time': task['estimated_time']
        })

@app.route('/')
def index():
    return send_file('templates/index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    """파일 업로드 처리"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': '파일이 선택되지 않았습니다'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': '파일이 선택되지 않았습니다'}), 400
        
        # 파일 타입 확인
        file_type = None
        if allowed_file(file.filename, ALLOWED_VIDEO_EXTENSIONS):
            file_type = 'video'
        elif allowed_file(file.filename, ALLOWED_IMAGE_EXTENSIONS):
            file_type = 'image'
        elif allowed_file(file.filename, ALLOWED_AUDIO_EXTENSIONS):
            file_type = 'audio'
        else:
            return jsonify({'error': '지원하지 않는 파일 형식입니다'}), 400
        
        # 파일 저장
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4()}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(filepath)
        
        return jsonify({
            'message': '파일이 성공적으로 업로드되었습니다',
            'filename': unique_filename,
            'type': file_type,
            'original_name': filename
        })
    
    except Exception as e:
        return jsonify({'error': f'업로드 중 오류가 발생했습니다: {str(e)}'}), 500

@app.route('/process', methods=['POST'])
def process_video():
    """비디오 처리 (합치기, 음악 추가, 자막 추가)"""
    try:
        data = request.json
        operation = data.get('operation')
        
        # 작업 ID 생성
        task_id = str(uuid.uuid4())
        
        if operation == 'concatenate':
            # 백그라운드에서 실행
            thread = threading.Thread(target=concatenate_media_with_progress, args=(data, task_id))
            thread.start()
            return jsonify({'task_id': task_id, 'message': '비디오 합치기 작업이 시작되었습니다'})
        elif operation == 'add_audio':
            thread = threading.Thread(target=add_audio_to_video_with_progress, args=(data, task_id))
            thread.start()
            return jsonify({'task_id': task_id, 'message': '배경음악 추가 작업이 시작되었습니다'})
        elif operation == 'add_subtitle':
            thread = threading.Thread(target=add_subtitle_to_video_with_progress, args=(data, task_id))
            thread.start()
            return jsonify({'task_id': task_id, 'message': '자막 추가 작업이 시작되었습니다'})
        elif operation == 'create_final_video':
            thread = threading.Thread(target=create_final_video_with_progress, args=(data, task_id))
            thread.start()
            return jsonify({'task_id': task_id, 'message': '최종 비디오 생성 작업이 시작되었습니다'})
        else:
            return jsonify({'error': '지원하지 않는 작업입니다'}), 400
    except Exception as e:
        return jsonify({'error': f'처리 중 오류가 발생했습니다: {str(e)}'}), 500

def concatenate_media(data):
    """영상/이미지 합치기"""
    files = data.get('files', [])
    if len(files) < 2:
        return jsonify({'error': '최소 2개의 파일이 필요합니다'}), 400
    
    clips = []
    
    for file_info in files:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file_info['filename'])
        
        if file_info['type'] == 'video':
            clip = safe_load_video(filepath)
        elif file_info['type'] == 'image':
            # 이미지는 3초 동안 표시
            duration = file_info.get('duration', 3)
            clip = ImageClip(filepath, duration=duration)
        
        clips.append(clip)
    
    # 클립들을 연결
    final_clip = concatenate_videoclips(clips, method="compose")
    
    # 출력 파일명 생성
    output_filename = f"concatenated_{uuid.uuid4()}.mp4"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
    
    # 비디오 저장
    safe_write_videofile(final_clip, output_path)
    
    # 메모리 정리
    for clip in clips:
        clip.close()
    final_clip.close()
    
    return jsonify({
        'message': '비디오가 성공적으로 합쳐졌습니다',
        'output_file': output_filename
    })

def add_audio_to_video(data):
    """여러 영상을 합치고 전체에 배경음악 추가"""
    files = data.get('files', [])
    audio_file = data.get('audio_file')
    
    if len(files) < 1:
        return jsonify({'error': '최소 1개의 비디오/이미지 파일이 필요합니다'}), 400
    
    if not audio_file:
        return jsonify({'error': '배경음악 파일이 필요합니다'}), 400
    
    clips = []
    
    # 모든 비디오/이미지 클립 로드
    for file_info in files:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file_info['filename'])
        
        if file_info['type'] == 'video':
            clip = safe_load_video(filepath)
        elif file_info['type'] == 'image':
            # 이미지는 지정된 시간 또는 기본 3초 동안 표시
            duration = file_info.get('duration', 3)
            clip = ImageClip(filepath, duration=duration)
        
        clips.append(clip)
    
    # 모든 클립을 연결하여 하나의 비디오로 만들기
    if len(clips) > 1:
        combined_video = concatenate_videoclips(clips, method="compose")
    else:
        combined_video = clips[0]
    
    # 배경음악 로드
    audio_path = os.path.join(app.config['UPLOAD_FOLDER'], audio_file)
    audio_clip = safe_load_audio(audio_path)
    
    # 배경음악을 비디오 길이에 맞춤
    if audio_clip.duration > combined_video.duration:
        # 음악이 더 길면 비디오 길이에 맞춰 자르기
        audio_clip = audio_clip.subclipped(0, combined_video.duration)
    else:
        # 음악이 더 짧으면 반복하여 비디오 길이에 맞춤
        loops_needed = int(combined_video.duration / audio_clip.duration) + 1
        repeated_clips = [audio_clip] * loops_needed
        audio_clip = concatenate_audioclips(repeated_clips).subclipped(0, combined_video.duration)
    
    # 비디오에 배경음악 추가
    final_clip = combined_video.with_audio(audio_clip)
    
    # 출력 파일명 생성
    output_filename = f"with_background_music_{uuid.uuid4()}.mp4"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
    
    # 비디오 저장
    safe_write_videofile(final_clip, output_path)
    
    # 메모리 정리
    for clip in clips:
        clip.close()
    combined_video.close()
    audio_clip.close()
    final_clip.close()
    
    return jsonify({
        'message': '배경음악이 포함된 비디오가 성공적으로 생성되었습니다',
        'output_file': output_filename
    })

def add_subtitle_to_video(data):
    """비디오에 자막 추가"""
    video_file = data.get('video_file')
    subtitle_text = data.get('subtitle_text', '')
    start_time = data.get('start_time', 0)
    end_time = data.get('end_time', 5)
    
    if not video_file:
        return jsonify({'error': '비디오 파일이 필요합니다'}), 400
    
    video_path = os.path.join(app.config['UPLOAD_FOLDER'], video_file)
    
    # 비디오 로드
    video_clip = safe_load_video(video_path)
    
    # 자막 생성
    txt_clip = create_text_clip_safe(
        subtitle_text,
        font_size=50,
        color='white',
        stroke_color='black',
        stroke_width=2
    ).with_position(('center', 'bottom')).with_duration(end_time - start_time).with_start(start_time)
    
    # 비디오에 자막 합성
    final_clip = CompositeVideoClip([video_clip, txt_clip])
    
    # 출력 파일명 생성
    output_filename = f"with_subtitle_{uuid.uuid4()}.mp4"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
    
    # 비디오 저장
    safe_write_videofile(final_clip, output_path)
    
    # 메모리 정리
    video_clip.close()
    txt_clip.close()
    final_clip.close()
    
    return jsonify({
        'message': '자막이 성공적으로 추가되었습니다',
        'output_file': output_filename
    })

@handle_subprocess_errors
def create_final_video_with_progress(data, task_id):
    """모든 요소를 포함한 최종 비디오 생성 (진행상황 추적)"""
    try:
        # 작업 초기화
        task = task_manager.create_task(task_id, 'create_final_video', 100)
        
        files = data.get('files', [])
        audio_file = data.get('audio_file')
        audio_volume = data.get('audio_volume', 50)
        subtitles = data.get('subtitles', [])
        output_quality = data.get('output_quality', 'medium')
        video_title = data.get('video_title', 'Final Video')
        
        if len(files) < 1:
            task_manager.set_status(task_id, 'error', '최소 1개의 비디오/이미지 파일이 필요합니다')
            return
        
        clips = []
        total_steps = len(files) + (10 if audio_file else 0) + (len(subtitles) * 2) + 30
        task_manager.tasks[task_id]['total_steps'] = total_steps
        current_step = 0
        
        # 1단계: 모든 파일을 클립으로 변환
        task_manager.update_progress(task_id, current_step, "파일을 로딩 중...")
        for i, file_info in enumerate(files):
            if task_manager.is_cancelled(task_id):
                return
            task_manager.wait_if_paused(task_id)
            
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], file_info['filename'])
            
            if file_info['type'] == 'video':
                clip = safe_load_video(filepath)
            elif file_info['type'] == 'image':
                duration = file_info.get('duration', 3)
                clip = ImageClip(filepath, duration=duration)
            
            clips.append(clip)
            current_step += 1
            task_manager.update_progress(task_id, current_step, f"파일 {i+1}/{len(files)} 로딩 완료")
        
        # 2단계: 클립들을 연결
        if task_manager.is_cancelled(task_id):
            return
        task_manager.wait_if_paused(task_id)
        
        task_manager.update_progress(task_id, current_step, "비디오 클립을 연결 중...")
        if len(clips) > 1:
            final_clip = concatenate_videoclips(clips, method="compose")
        else:
            final_clip = clips[0]
        current_step += 10
        task_manager.update_progress(task_id, current_step, "비디오 연결 완료")
        
        # 3단계: 배경음악 추가 (있는 경우)
        if audio_file:
            if task_manager.is_cancelled(task_id):
                return
            task_manager.wait_if_paused(task_id)
            
            task_manager.update_progress(task_id, current_step, "배경음악을 처리 중...")
            audio_path = os.path.join(app.config['UPLOAD_FOLDER'], audio_file)
            audio_clip = safe_load_audio(audio_path)
            current_step += 2
            
            # 오디오 볼륨 조정
            if volumex is not None:
                audio_clip = volumex(audio_clip, audio_volume / 100.0)
            current_step += 2
            task_manager.update_progress(task_id, current_step, "오디오 볼륨 조정 완료")
            
            # 오디오 길이 조정
            if audio_clip.duration > final_clip.duration:
                audio_clip = audio_clip.subclipped(0, final_clip.duration)
            elif audio_clip.duration < final_clip.duration:
                loops = int(final_clip.duration / audio_clip.duration) + 1
                repeated_clips = [audio_clip] * loops
                audio_clip = concatenate_audioclips(repeated_clips).subclipped(0, final_clip.duration)
            current_step += 3
            task_manager.update_progress(task_id, current_step, "오디오 길이 조정 완료")
            
            # 오디오 믹싱
            if final_clip.audio is not None:
                if CompositeAudioClip is not None:
                    try:
                        if volumex is not None:
                            original_audio = volumex(final_clip.audio, 0.7)
                            background_audio = volumex(audio_clip, 0.3)
                        else:
                            original_audio = final_clip.audio
                            background_audio = audio_clip
                        final_audio = CompositeAudioClip([original_audio, background_audio])
                    except Exception as e:
                        print(f"Warning: 오디오 믹싱 중 오류 발생: {e}. 배경음악만 사용합니다.")
                        final_audio = audio_clip
                else:
                    final_audio = audio_clip
            else:
                final_audio = audio_clip
            
            final_clip = final_clip.with_audio(final_audio)
            audio_clip.close()
            current_step += 3
            task_manager.update_progress(task_id, current_step, "배경음악 추가 완료")
        
        # 4단계: 자막 추가 (있는 경우)
        if subtitles:
            if task_manager.is_cancelled(task_id):
                return
            task_manager.wait_if_paused(task_id)
            
            task_manager.update_progress(task_id, current_step, "자막을 추가 중...")
            video_clips = [final_clip]
            
            for i, subtitle in enumerate(subtitles):
                if task_manager.is_cancelled(task_id):
                    return
                task_manager.wait_if_paused(task_id)
                
                try:
                    txt_clip = create_text_clip_safe(
                        subtitle['text'],
                        font_size=50,
                        color='white',
                        stroke_color='black',
                        stroke_width=2
                    ).with_position(('center', 'bottom')).with_duration(
                        subtitle['end_time'] - subtitle['start_time']
                    ).with_start(subtitle['start_time'])
                except Exception as e:
                    # 안전한 함수 실행에도 실패하면 로그만 남기고 건너뛰기
                    print(f"Error: 자막 생성 완전 실패: {e}")
                    continue
                
                video_clips.append(txt_clip)
                current_step += 2
                task_manager.update_progress(task_id, current_step, f"자막 {i+1}/{len(subtitles)} 추가 완료")
            
            final_clip = CompositeVideoClip(video_clips)
        
        # 5단계: 비디오 저장
        if task_manager.is_cancelled(task_id):
            return
        task_manager.wait_if_paused(task_id)
        
        task_manager.update_progress(task_id, current_step, "최종 비디오를 저장 중...")
        
        # 출력 설정
        codec_settings = {
            '480p': {'width': 854, 'height': 480, 'bitrate': '1000k'},
            '720p': {'width': 1280, 'height': 720, 'bitrate': '2500k'},
            '1080p': {'width': 1920, 'height': 1080, 'bitrate': '5000k'},
            'custom': {'bitrate': '3000k'}  # 사용자 정의는 기본 비트레이트만
        }
        
        output_setting = codec_settings.get(output_quality, codec_settings['720p'])
        
        # 사용자 정의 해상도 처리
        if output_quality == 'custom' and 'custom_resolution' in data:
            custom_res = data['custom_resolution']
            output_setting['width'] = custom_res['width']
            output_setting['height'] = custom_res['height']
        
        # 해상도 조정이 필요한 경우 적용
        if 'width' in output_setting and 'height' in output_setting:
            final_clip = final_clip.resized((output_setting['width'], output_setting['height']))
        
        bitrate = output_setting['bitrate']
        
        safe_title = "".join(c for c in video_title if c.isalnum() or c in (' ', '-', '_')).rstrip()[:20]
        if not safe_title:
            safe_title = "Final_Video"
        
        output_filename = f"{safe_title}_{uuid.uuid4().hex[:8]}.mp4"
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
        
        # 커스텀 진행상황 콜백을 사용하여 비디오 저장
        def progress_callback(chunk, file_size):
            if task_manager.is_cancelled(task_id):
                return False  # 작업 중단
            task_manager.wait_if_paused(task_id)
            # 저장 진행상황 업데이트 (마지막 20%에 해당)
            save_progress = int((chunk / file_size) * 20)
            total_progress = current_step + save_progress
            task_manager.update_progress(task_id, total_progress, f"비디오 저장 중... {int((chunk/file_size)*100)}%")
        
        # 비디오 저장
        safe_write_videofile(
            final_clip,
            output_path, 
            bitrate=bitrate,
            temp_audiofile=f'temp-audio-{uuid.uuid4().hex[:8]}.m4a'
        )
        
        # 메모리 정리
        for clip in clips:
            clip.close()
        final_clip.close()
        
        if not task_manager.is_cancelled(task_id):
            task_manager.update_progress(task_id, 100, f'"{video_title}" 최종 영상이 성공적으로 생성되었습니다')
            task_manager.set_status(task_id, 'completed', '작업이 완료되었습니다')
            # 결과 전송
            socketio.emit('task_completed', {
                'task_id': task_id,
                'output_file': output_filename,
                'message': f'"{video_title}" 최종 영상이 성공적으로 생성되었습니다'
            })
    
    except Exception as e:
        task_manager.set_status(task_id, 'error', f'오류가 발생했습니다: {str(e)}')
        socketio.emit('task_error', {
            'task_id': task_id,
            'error': str(e)
        })

@handle_subprocess_errors
def concatenate_media_with_progress(data, task_id):
    """영상/이미지 합치기 (진행상황 추적)"""
    try:
        task = task_manager.create_task(task_id, 'concatenate', 100)
        files = data.get('files', [])
        
        if len(files) < 2:
            task_manager.set_status(task_id, 'error', '최소 2개의 파일이 필요합니다')
            return
        
        clips = []
        total_steps = len(files) + 20
        task_manager.tasks[task_id]['total_steps'] = total_steps
        current_step = 0
        
        # 파일 로딩
        for i, file_info in enumerate(files):
            if task_manager.is_cancelled(task_id):
                return
            task_manager.wait_if_paused(task_id)
            
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], file_info['filename'])
            
            if file_info['type'] == 'video':
                clip = safe_load_video(filepath)
            elif file_info['type'] == 'image':
                duration = file_info.get('duration', 3)
                clip = ImageClip(filepath, duration=duration)
            
            clips.append(clip)
            current_step += 1
            task_manager.update_progress(task_id, current_step, f"파일 {i+1}/{len(files)} 로딩 완료")
        
        # 클립 연결
        if task_manager.is_cancelled(task_id):
            return
        task_manager.wait_if_paused(task_id)
        
        task_manager.update_progress(task_id, current_step, "비디오를 합치는 중...")
        final_clip = concatenate_videoclips(clips, method="compose")
        current_step += 10
        
        # 파일 저장
        output_filename = f"concatenated_{uuid.uuid4()}.mp4"
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
        
        task_manager.update_progress(task_id, current_step, "비디오를 저장 중...")
        safe_write_videofile(final_clip, output_path)
        
        # 메모리 정리
        for clip in clips:
            clip.close()
        final_clip.close()
        
        if not task_manager.is_cancelled(task_id):
            task_manager.update_progress(task_id, 100, '비디오가 성공적으로 합쳐졌습니다')
            task_manager.set_status(task_id, 'completed', '작업이 완료되었습니다')
            socketio.emit('task_completed', {
                'task_id': task_id,
                'output_file': output_filename,
                'message': '비디오가 성공적으로 합쳐졌습니다'
            })
    
    except Exception as e:
        task_manager.set_status(task_id, 'error', f'오류가 발생했습니다: {str(e)}')

@handle_subprocess_errors
def add_audio_to_video_with_progress(data, task_id):
    """배경음악 추가 (진행상황 추적)"""
    try:
        task = task_manager.create_task(task_id, 'add_audio', 100)
        files = data.get('files', [])
        audio_file = data.get('audio_file')
        
        if len(files) < 1:
            task_manager.set_status(task_id, 'error', '최소 1개의 비디오/이미지 파일이 필요합니다')
            return
        
        if not audio_file:
            task_manager.set_status(task_id, 'error', '배경음악 파일이 필요합니다')
            return
        
        current_step = 0
        
        # 비디오 클립 로딩
        clips = []
        for i, file_info in enumerate(files):
            if task_manager.is_cancelled(task_id):
                return
            task_manager.wait_if_paused(task_id)
            
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], file_info['filename'])
            
            if file_info['type'] == 'video':
                clip = safe_load_video(filepath)
            elif file_info['type'] == 'image':
                duration = file_info.get('duration', 3)
                clip = ImageClip(filepath, duration=duration)
            
            clips.append(clip)
            current_step += 20
            task_manager.update_progress(task_id, current_step, f"비디오 파일 {i+1}/{len(files)} 로딩 완료")
        
        # 비디오 합치기
        if task_manager.is_cancelled(task_id):
            return
        task_manager.wait_if_paused(task_id)
        
        if len(clips) > 1:
            combined_video = concatenate_videoclips(clips, method="compose")
        else:
            combined_video = clips[0]
        current_step += 20
        task_manager.update_progress(task_id, current_step, "배경음악을 로딩 중...")
        
        # 배경음악 처리
        audio_path = os.path.join(app.config['UPLOAD_FOLDER'], audio_file)
        audio_clip = safe_load_audio(audio_path)
        current_step += 20
        
        # 음악 길이 조정
        if audio_clip.duration > combined_video.duration:
            audio_clip = audio_clip.subclipped(0, combined_video.duration)
        else:
            loops_needed = int(combined_video.duration / audio_clip.duration) + 1
            repeated_clips = [audio_clip] * loops_needed
            audio_clip = concatenate_audioclips(repeated_clips).subclipped(0, combined_video.duration)
        current_step += 20
        task_manager.update_progress(task_id, current_step, "배경음악을 추가 중...")
        
        # 최종 비디오 생성
        final_clip = combined_video.with_audio(audio_clip)
        
        # 저장
        output_filename = f"with_background_music_{uuid.uuid4()}.mp4"
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
        
        task_manager.update_progress(task_id, current_step, "비디오를 저장 중...")
        safe_write_videofile(final_clip, output_path)
        
        # 메모리 정리
        for clip in clips:
            clip.close()
        combined_video.close()
        audio_clip.close()
        final_clip.close()
        
        if not task_manager.is_cancelled(task_id):
            task_manager.update_progress(task_id, 100, '배경음악이 포함된 비디오가 성공적으로 생성되었습니다')
            task_manager.set_status(task_id, 'completed', '작업이 완료되었습니다')
            socketio.emit('task_completed', {
                'task_id': task_id,
                'output_file': output_filename,
                'message': '배경음악이 포함된 비디오가 성공적으로 생성되었습니다'
            })
    
    except Exception as e:
        task_manager.set_status(task_id, 'error', f'오류가 발생했습니다: {str(e)}')

def add_subtitle_to_video_with_progress(data, task_id):
    """자막 추가 (진행상황 추적)"""
    try:
        task = task_manager.create_task(task_id, 'add_subtitle', 100)
        video_file = data.get('video_file')
        subtitle_text = data.get('subtitle_text', '')
        start_time = data.get('start_time', 0)
        end_time = data.get('end_time', 5)
        
        if not video_file:
            task_manager.set_status(task_id, 'error', '비디오 파일이 필요합니다')
            return
        
        current_step = 0
        
        # 비디오 로드
        task_manager.update_progress(task_id, current_step, "비디오를 로딩 중...")
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], video_file)
        video_clip = safe_load_video(video_path)
        current_step += 40
        
        # 자막 생성
        task_manager.update_progress(task_id, current_step, "자막을 생성 중...")
        txt_clip = create_text_clip_safe(
            subtitle_text,
            font_size=50,
            color='white',
            stroke_color='black',
            stroke_width=2
        ).with_position(('center', 'bottom')).with_duration(end_time - start_time).with_start(start_time)
        current_step += 20
        
        # 자막 합성
        task_manager.update_progress(task_id, current_step, "자막을 비디오에 합성 중...")
        final_clip = CompositeVideoClip([video_clip, txt_clip])
        current_step += 20
        
        # 저장
        output_filename = f"with_subtitle_{uuid.uuid4()}.mp4"
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
        
        task_manager.update_progress(task_id, current_step, "비디오를 저장 중...")
        safe_write_videofile(final_clip, output_path)
        
        # 메모리 정리
        video_clip.close()
        txt_clip.close()
        final_clip.close()
        
        if not task_manager.is_cancelled(task_id):
            task_manager.update_progress(task_id, 100, '자막이 성공적으로 추가되었습니다')
            task_manager.set_status(task_id, 'completed', '작업이 완료되었습니다')
            socketio.emit('task_completed', {
                'task_id': task_id,
                'output_file': output_filename,
                'message': '자막이 성공적으로 추가되었습니다'
            })
    
    except Exception as e:
        task_manager.set_status(task_id, 'error', f'오류가 발생했습니다: {str(e)}')

# 기존 함수들 (호환성을 위해 유지)
def create_final_video(data):
    files = data.get('files', [])
    audio_file = data.get('audio_file')
    audio_volume = data.get('audio_volume', 50)
    subtitles = data.get('subtitles', [])
    output_quality = data.get('output_quality', 'medium')
    video_title = data.get('video_title', 'Final Video')
    
    if len(files) < 1:
        return jsonify({'error': '최소 1개의 비디오/이미지 파일이 필요합니다'}), 400
    
    clips = []
    
    # 1단계: 모든 파일을 클립으로 변환
    for file_info in files:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file_info['filename'])
        
        if file_info['type'] == 'video':
            clip = safe_load_video(filepath)
        elif file_info['type'] == 'image':
            duration = file_info.get('duration', 3)
            clip = ImageClip(filepath, duration=duration)
        
        clips.append(clip)
    
    # 2단계: 클립들을 연결
    if len(clips) > 1:
        final_clip = concatenate_videoclips(clips, method="compose")
    else:
        final_clip = clips[0]
    
    # 3단계: 배경음악 추가 (있는 경우)
    if audio_file:
        audio_path = os.path.join(app.config['UPLOAD_FOLDER'], audio_file)
        audio_clip = safe_load_audio(audio_path)
        
        # 오디오 볼륨 조정
        if volumex is not None:
            audio_clip = volumex(audio_clip, audio_volume / 100.0)
        else:
            # volumex가 없으면 다른 방법 시도
            try:
                # AudioClip의 내장 메서드 시도
                audio_clip = audio_clip.volumex(audio_volume / 100.0)
            except AttributeError:
                print(f"Warning: 볼륨 조정을 할 수 없습니다. 원본 볼륨으로 사용합니다.")
                pass
        
        # 오디오가 비디오보다 길면 자르고, 짧으면 반복
        if audio_clip.duration > final_clip.duration:
            audio_clip = audio_clip.subclipped(0, final_clip.duration)
        elif audio_clip.duration < final_clip.duration:
            # 오디오를 반복해서 비디오 길이에 맞춤
            loops = int(final_clip.duration / audio_clip.duration) + 1
            repeated_clips = [audio_clip] * loops
            audio_clip = concatenate_audioclips(repeated_clips).subclipped(0, final_clip.duration)
        
        # 기존 오디오와 배경음악 믹싱 (기존 오디오가 있는 경우)
        if final_clip.audio is not None:
            if CompositeAudioClip is not None:
                # 볼륨 조정을 안전하게 처리
                try:
                    if volumex is not None:
                        original_audio = volumex(final_clip.audio, 0.7)
                        background_audio = volumex(audio_clip, 0.3)
                    else:
                        # volumex가 없으면 multiply_volume 시도
                        try:
                            original_audio = final_clip.audio.multiply_volume(0.7)
                            background_audio = audio_clip.multiply_volume(0.3)
                        except AttributeError:
                            # multiply_volume도 없으면 원본 볼륨 사용
                            original_audio = final_clip.audio
                            background_audio = audio_clip
                    
                    final_audio = CompositeAudioClip([original_audio, background_audio])
                except Exception as e:
                    print(f"Warning: 오디오 믹싱 중 오류 발생: {e}. 배경음악만 사용합니다.")
                    final_audio = audio_clip
            else:
                # CompositeAudioClip이 없으면 배경음악만 사용
                final_audio = audio_clip
        else:
            final_audio = audio_clip
        
        final_clip = final_clip.with_audio(final_audio)
        audio_clip.close()
    
    # 4단계: 자막 추가 (있는 경우)
    if subtitles:
        video_clips = [final_clip]
        
        for subtitle in subtitles:
            try:
                txt_clip = create_text_clip_safe(
                    subtitle['text'],
                    font_size=50,
                    color='white',
                    stroke_color='black',
                    stroke_width=2
                ).with_position(('center', 'bottom')).with_duration(
                    subtitle['end_time'] - subtitle['start_time']
                ).with_start(subtitle['start_time'])
            except Exception as e:
                # 안전한 함수 실행에도 실패하면 로그만 남기고 건너뛰기
                print(f"Error: 자막 생성 완전 실패: {e}")
                continue
            
            video_clips.append(txt_clip)
        
        final_clip = CompositeVideoClip(video_clips)
    
    # 5단계: 출력 품질 설정
    codec_settings = {
        'low': {'bitrate': '500k'},
        'medium': {'bitrate': '1000k'},
        'high': {'bitrate': '2000k'}
    }
    
    bitrate = codec_settings.get(output_quality, codec_settings['medium'])['bitrate']
    
    # 출력 파일명 생성
    safe_title = "".join(c for c in video_title if c.isalnum() or c in (' ', '-', '_')).rstrip()[:20]
    if not safe_title:
        safe_title = "Final_Video"
    
    output_filename = f"{safe_title}_{uuid.uuid4().hex[:8]}.mp4"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
    
    # 비디오 저장
    safe_write_videofile(
        final_clip,
        output_path, 
        bitrate=bitrate,
        temp_audiofile=f'temp-audio-{uuid.uuid4().hex[:8]}.m4a'
    )
    
    # 메모리 정리
    for clip in clips:
        clip.close()
    final_clip.close()
    
    return jsonify({
        'message': f'"{video_title}" 최종 영상이 성공적으로 생성되었습니다',
        'output_file': output_filename
    })

@app.route('/download/<filename>')
def download_file(filename):
    """처리된 파일 다운로드"""
    try:
        return send_file(
            os.path.join(app.config['OUTPUT_FOLDER'], filename),
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'error': '파일을 찾을 수 없습니다'}), 404

@app.route('/files')
def list_files():
    """업로드된 파일 목록 조회"""
    try:
        files = []
        for filename in os.listdir(app.config['UPLOAD_FOLDER']):
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.isfile(file_path):
                # 파일 타입 확인
                file_type = None
                if allowed_file(filename, ALLOWED_VIDEO_EXTENSIONS):
                    file_type = 'video'
                elif allowed_file(filename, ALLOWED_IMAGE_EXTENSIONS):
                    file_type = 'image'
                elif allowed_file(filename, ALLOWED_AUDIO_EXTENSIONS):
                    file_type = 'audio'
                
                files.append({
                    'filename': filename,
                    'type': file_type,
                    'size': os.path.getsize(file_path)
                })
        
        return jsonify({'files': files})
    
    except Exception as e:
        return jsonify({'error': f'파일 목록을 불러올 수 없습니다: {str(e)}'}), 500

if __name__ == '__main__':
    print("=== MoviePy 웹 비디오 에디터 ===")
    print("✅ MoviePy가 정상적으로 로드되었습니다.")
    print("🎬 모든 비디오 편집 기능을 사용할 수 있습니다.")
    print("🔄 실시간 진행상황 추적 기능이 활성화되었습니다.")
    print("🌐 브라우저에서 http://localhost:5000 으로 접속하세요")
    
    # 안전한 서버 실행
    try:
        socketio.run(app, debug=True, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)
    except Exception as e:
        print(f"서버 실행 중 오류: {e}")
