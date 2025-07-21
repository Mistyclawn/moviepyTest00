#!/usr/bin/env python3
"""
MoviePy 설치 및 테스트 스크립트
"""

def test_moviepy():
    try:
        print("MoviePy 모듈을 가져오는 중...")
        from moviepy.editor import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, concatenate_videoclips, ImageClip
        print("✅ MoviePy 모듈을 성공적으로 가져왔습니다!")
        
        # 간단한 테스트
        print("MoviePy 기능을 테스트하는 중...")
        
        # ImageClip 테스트 (파일 없이도 가능)
        import numpy as np
        dummy_array = np.zeros((100, 100, 3), dtype=np.uint8)
        test_clip = ImageClip(dummy_array, duration=1)
        print(f"✅ ImageClip 생성 성공: {test_clip.duration}초")
        
        # TextClip 테스트
        try:
            text_clip = TextClip("Test", fontsize=50, color='white', duration=1)
            print("✅ TextClip 생성 성공")
        except Exception as e:
            print(f"⚠️  TextClip 생성 실패 (폰트 문제일 수 있음): {e}")
        
        test_clip.close()
        print("✅ MoviePy가 정상적으로 작동합니다!")
        return True
        
    except ImportError as e:
        print(f"❌ MoviePy 모듈을 찾을 수 없습니다: {e}")
        return False
    except Exception as e:
        print(f"❌ MoviePy 테스트 중 오류 발생: {e}")
        return False

def install_moviepy():
    """MoviePy 설치 시도"""
    import subprocess
    import sys
    
    print("MoviePy 설치를 시도합니다...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "moviepy"])
        print("✅ MoviePy 설치 완료!")
        return True
    except Exception as e:
        print(f"❌ MoviePy 설치 실패: {e}")
        return False

if __name__ == "__main__":
    print("=== MoviePy 환경 테스트 ===")
    
    if not test_moviepy():
        print("\nMoviePy 설치를 시도합니다...")
        if install_moviepy():
            print("\n재테스트합니다...")
            test_moviepy()
        else:
            print("\n수동으로 다음 명령어를 실행해보세요:")
            print("pip install moviepy")
            print("또는")
            print("conda install -c conda-forge moviepy")
    
    print("\n=== 테스트 완료 ===")
