from flask import Flask, request, jsonify, send_file, render_template_string
from flask_cors import CORS
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
    """비디오 처리 (MoviePy 기능은 임시로 비활성화)"""
    try:
        data = request.json
        operation = data.get('operation')
        
        # MoviePy 설치 문제로 임시로 더미 응답 반환
        return jsonify({
            'message': f'{operation} 작업이 완료되었습니다 (데모 모드)',
            'output_file': f'demo_{uuid.uuid4()}.mp4',
            'note': 'MoviePy 환경 설정 후 실제 비디오 처리가 가능합니다'
        })
    
    except Exception as e:
        return jsonify({'error': f'처리 중 오류가 발생했습니다: {str(e)}'}), 500

@app.route('/download/<filename>')
def download_file(filename):
    """처리된 파일 다운로드"""
    try:
        # 데모용 - 실제 파일 대신 메시지 반환
        return jsonify({
            'message': '데모 모드입니다. MoviePy 환경 설정 후 실제 파일 다운로드가 가능합니다.',
            'filename': filename
        })
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

@app.route('/test')
def test():
    """테스트 페이지"""
    return jsonify({
        'message': 'Flask 서버가 정상적으로 작동하고 있습니다!',
        'status': 'OK',
        'note': 'MoviePy 환경 설정 후 모든 기능을 사용할 수 있습니다.'
    })

if __name__ == '__main__':
    print("MoviePy 웹 비디오 에디터 서버를 시작합니다...")
    print("브라우저에서 http://localhost:5000 으로 접속하세요")
    print("테스트: http://localhost:5000/test")
    app.run(debug=True, host='0.0.0.0', port=5000)
