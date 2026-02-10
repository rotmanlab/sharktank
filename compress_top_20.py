import os
import subprocess
import time

def get_file_size(file_path):
    return os.path.getsize(file_path)

def compress_video(input_path):
    # Temp output path
    dirname, filename = os.path.split(input_path)
    temp_path = os.path.join(dirname, "temp_" + filename)
    
    print(f"--> Compressing: {filename} ({(get_file_size(input_path)/(1024*1024)):.1f} MB)")
    
    # FFmpeg command optimized for Apple Silicon (h264_videotoolbox)
    # -crf doesn't apply to videotoolbox in the same way, so we use -q:v (quality) 
    # approx 60-65 is good for web. Lower is better quality, higher is smaller.
    # Actually, let's stick to software encoding (libx264) with crf 28. 
    # It is slightly slower than hardware but produces MUCH better compression/size ratios for web.
    # M2 Pro is fast enough that software encoding is still very fast.
    
    cmd = [
        "ffmpeg",
        "-y", # Overwrite output if exists
        "-i", input_path,
        "-vcodec", "libx264", 
        "-crf", "28", # Aggressive but visually okay for web (standard is 23)
        "-preset", "veryfast", # Faster encoding
        "-acodec", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart", # Optimize for web streaming
        "-loglevel", "error",
        temp_path
    ]
    
    start_time = time.time()
    try:
        subprocess.run(cmd, check=True)
        duration = time.time() - start_time
        
        # Check if compression actually helped
        old_size = get_file_size(input_path)
        new_size = get_file_size(temp_path)
        
        if new_size < old_size:
            os.replace(temp_path, input_path)
            print(f"    Done in {duration:.1f}s. Size: {(old_size/(1024*1024)):.1f}MB -> {(new_size/(1024*1024)):.1f}MB (-{((old_size-new_size)/old_size)*100:.0f}%)")
            return True
        else:
            print(f"    Compressed file was larger. Keeping original.")
            os.remove(temp_path)
            return False
            
    except subprocess.CalledProcessError:
        print(f"    Error compressing {filename}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return False

def main():
    root_dir = "."
    mp4_files = []
    
    print("Scanning for MP4 files...")
    for dirpath, _, filenames in os.walk(root_dir):
        for f in filenames:
            if f.lower().endswith(".mp4"):
                full_path = os.path.join(dirpath, f)
                mp4_files.append(full_path)
    
    # Sort by size (descending)
    mp4_files.sort(key=lambda x: get_file_size(x), reverse=True)
    
    # Take top 20
    batch_size = 20
    to_process = mp4_files[:batch_size]
    
    print(f"Found {len(mp4_files)} videos. Processing top {batch_size} largest...")
    print("-" * 50)
    
    start_global = time.time()
    for i, file_path in enumerate(to_process):
        print(f"[{i+1}/{batch_size}] Processing...")
        compress_video(file_path)
        
    print("-" * 50)
    print(f"Batch complete in {(time.time() - start_global):.1f} seconds.")

if __name__ == "__main__":
    main()
