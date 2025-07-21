from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os

# MoviePy import (editor 없이)
from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy.audio.io.AudioFileClip import AudioFileClip
from moviepy.video.VideoClip import ImageClip, TextClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
from moviepy import concatenate_videoclips

# CompositeAudioClip import 시도
try:
    from moviepy.audio.compositing.CompositeAudioClip import CompositeAudioClip
except ImportError:
    try:
        from moviepy.audio.AudioClip import CompositeAudioClip
    except ImportError:
        try:
            from moviepy import CompositeAudioClip
        except ImportError:
            CompositeAudioClip = None

import os
import tempfile
import uuid
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app)

# 설정
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB 제한

# 폴더 생성
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# 허용된 파일 확장자
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'webm'}
ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'bmp'}
ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'wav', 'aac', 'm4a', 'ogg'}

def allowed_file(filename, extensions):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in extensions

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
        if operation == 'concatenate':
            return concatenate_media(data)
        elif operation == 'add_audio':
            return add_audio_to_video(data)
        elif operation == 'add_subtitle':
            return add_subtitle_to_video(data)
        elif operation == 'create_final_video':
            return create_final_video(data)
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
            clip = VideoFileClip(filepath)
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
    final_clip.write_videofile(output_path, codec='libx264', audio_codec='aac')
    
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
            clip = VideoFileClip(filepath)
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
    audio_clip = AudioFileClip(audio_path)
    
    # 배경음악을 비디오 길이에 맞춤
    if audio_clip.duration > combined_video.duration:
        # 음악이 더 길면 비디오 길이에 맞춰 자르기
        audio_clip = audio_clip.subclip(0, combined_video.duration)
    else:
        # 음악이 더 짧으면 반복하여 비디오 길이에 맞춤
        audio_clip = audio_clip.loop(duration=combined_video.duration)
    
    # 비디오에 배경음악 추가
    final_clip = combined_video.set_audio(audio_clip)
    
    # 출력 파일명 생성
    output_filename = f"with_background_music_{uuid.uuid4()}.mp4"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
    
    # 비디오 저장
    final_clip.write_videofile(output_path, codec='libx264', audio_codec='aac')
    
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
    video_clip = VideoFileClip(video_path)
    
    # 자막 생성
    txt_clip = TextClip(subtitle_text, 
                       fontsize=50, 
                       color='white', 
                       font='Arial-Bold',
                       stroke_color='black',
                       stroke_width=2)
    
    # 자막 위치와 시간 설정
    txt_clip = txt_clip.set_position(('center', 'bottom')).set_duration(end_time - start_time).set_start(start_time)
    
    # 비디오에 자막 합성
    final_clip = CompositeVideoClip([video_clip, txt_clip])
    
    # 출력 파일명 생성
    output_filename = f"with_subtitle_{uuid.uuid4()}.mp4"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
    
    # 비디오 저장
    final_clip.write_videofile(output_path, codec='libx264', audio_codec='aac')
    
    # 메모리 정리
    video_clip.close()
    txt_clip.close()
    final_clip.close()
    
    return jsonify({
        'message': '자막이 성공적으로 추가되었습니다',
        'output_file': output_filename
    })

def create_final_video(data):
    """모든 요소를 포함한 최종 비디오 생성"""
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
            clip = VideoFileClip(filepath)
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
        audio_clip = AudioFileClip(audio_path)
        
        # 오디오 볼륨 조정
        audio_clip = audio_clip.volumex(audio_volume / 100.0)
        
        # 오디오가 비디오보다 길면 자르고, 짧으면 반복
        if audio_clip.duration > final_clip.duration:
            audio_clip = audio_clip.subclip(0, final_clip.duration)
        elif audio_clip.duration < final_clip.duration:
            # 오디오를 반복해서 비디오 길이에 맞춤
            loops = int(final_clip.duration / audio_clip.duration) + 1
            audio_clip = audio_clip.loop(loops).subclip(0, final_clip.duration)
        
        # 기존 오디오와 배경음악 믹싱 (기존 오디오가 있는 경우)
        if final_clip.audio is not None:
            if CompositeAudioClip is not None:
                final_audio = CompositeAudioClip([final_clip.audio.volumex(0.7), audio_clip.volumex(0.3)])
            else:
                # CompositeAudioClip이 없으면 배경음악만 사용
                final_audio = audio_clip
        else:
            final_audio = audio_clip
        
        final_clip = final_clip.set_audio(final_audio)
        audio_clip.close()
    
    # 4단계: 자막 추가 (있는 경우)
    if subtitles:
        video_clips = [final_clip]
        
        for subtitle in subtitles:
            txt_clip = TextClip(subtitle['text'], 
                               fontsize=50, 
                               color='white', 
                               font='Arial-Bold',
                               stroke_color='black',
                               stroke_width=2)
            
            # 자막 위치와 시간 설정
            txt_clip = txt_clip.set_position(('center', 'bottom')).set_duration(
                subtitle['end_time'] - subtitle['start_time']
            ).set_start(subtitle['start_time'])
            
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
    final_clip.write_videofile(
        output_path, 
        codec='libx264', 
        audio_codec='aac',
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
    print("🌐 브라우저에서 http://localhost:5000 으로 접속하세요")
    app.run(debug=True, host='0.0.0.0', port=5000)
