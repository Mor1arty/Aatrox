import asyncio
from dotenv import load_dotenv
import os
import aiohttp
import subprocess

async def join_voice_channel(bot_token: str, channel_id: str) -> dict:
    """
    加入指定的语音频道。

    :param bot_token: 机器人的认证令牌
    :param channel_id: 要加入的语音频道ID
    :return: API的响应结果
    """
    url = 'https://www.kaiheila.cn/api/v3/voice/join'
    headers = {
        'Authorization': f'Bot {bot_token}',
        'Content-Type': 'application/json'
    }
    data = {
        'channel_id': channel_id
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as response:
            result = await response.json()
            return result

async def leave_voice_channel(bot_token: str, channel_id: str) -> dict:
    """
    离开当前的语音频道。

    :param bot_token: 机器人的认证令牌
    :return: API的响应结果
    """
    url = 'https://www.kaiheila.cn/api/v3/voice/leave'
    headers = {
        'Authorization': f'Bot {bot_token}',
        'Content-Type': 'application/json'
    }
    data = {
        'channel_id': channel_id
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as response:
            result = await response.json()
            return result

async def stream_audio(join_response: dict, mp3_file: str) -> None:
    """
    根据加入语音频道的响应参数,使用ffmpeg推流本地MP3文件到语音频道。

    :param join_response: join_voice_channel函数的返回结果
    :param mp3_file: 要推流的本地MP3文件路径
    """
    data = join_response['data']
    audio_ssrc = data['audio_ssrc']
    audio_pt = data['audio_pt']
    ip = data['ip']
    port = data['port']
    rtcp_port = data['rtcp_port']
    bitrate = data['bitrate'] // 1000  # 将比特率从bps转换为kbps

    ffmpeg_command = [
        'ffmpeg',
        '-re',  # 以实时速率读取输入
        '-i', mp3_file,
        '-map', '0:a:0',
        '-acodec', 'libopus',
        '-ab', f'{bitrate}k',
        '-ac', '2',
        '-ar', '48000',
        '-filter:a', 'volume=0.5',
        '-f', 'tee',
        f'[select=a:f=rtp:ssrc={audio_ssrc}:payload_type={audio_pt}]rtp://{ip}:{port}?rtcpport={rtcp_port}'
    ]
    print(' '.join(ffmpeg_command))

    process = await asyncio.create_subprocess_exec(
        *ffmpeg_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    print("开始推流...")
    
    # 等待进程结束或者被中断
    try:
        await process.communicate()
    except asyncio.CancelledError:
        print("推流被中断")
        process.terminate()
        await process.wait()
    
    print("推流结束")

async def play_audio_in_channel(bot_token: str, channel_id: str, mp3_file: str):
    """
    加入语音频道,播放音频,然后退出频道。

    :param bot_token: 机器人的认证令牌
    :param channel_id: 要加入的语音频道ID
    :param mp3_file: 要播放的MP3文件路径
    """
    join_result = await join_voice_channel(bot_token, channel_id)
    print(f"加入语音频道API响应: {join_result}")

    if join_result['code'] == 0:
        streaming_task = asyncio.create_task(stream_audio(join_result, mp3_file))
        
        # 获取音频文件的时长
        audio_duration = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', mp3_file])
        audio_duration = float(audio_duration)
        print(f"音频时长: {audio_duration}秒")

        try:
            await asyncio.wait_for(streaming_task, timeout=audio_duration)
        except asyncio.TimeoutError:
            print("播放时间到，停止推流")
        
        # 停止推流
        streaming_task.cancel()
        await asyncio.sleep(1)  # 给一点时间让推流任务清理
        
        # 离开语音频道
        leave_result = await leave_voice_channel(bot_token, channel_id)
        print(f"离开语音频道API响应: {leave_result}")
    else:
        print("加入语音频道失败")

async def main():
    load_dotenv()
    BOT_TOKEN = os.getenv('BOT_TOKEN_DEV') or os.getenv('BOT_TOKEN')
    VOICE_CHANNEL_ID = os.getenv('VOICE_CHANNEL_ID')
    MP3_FILE = os.getenv('MP3_FILE')  # 从环境变量中读取MP3文件路径
    
    await play_audio_in_channel(BOT_TOKEN, VOICE_CHANNEL_ID, MP3_FILE)

if __name__ == "__main__":
    asyncio.run(main())
