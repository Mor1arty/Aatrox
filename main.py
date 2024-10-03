import json, random
import time
import asyncio, aiohttp
import logging
from champion_manager import champion_manager
from dotenv import load_dotenv
import os
from khl import Bot, Message, EventTypes, Event
from khl.card import CardMessage, Card, Module, Element, Types
import traceback
from app.lol.connector import connector
from kook_audio import play_audio_in_channel, join_voice_channel, leave_voice_channel

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 使用环境变量
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN_DEV') or os.getenv('BOT_TOKEN')
TEXT_CHANNEL_ID = os.getenv('TEXT_CHANNEL_ID')
VOICE_CHANNEL_ID = os.getenv('VOICE_CHANNEL_ID')
GUILD_ID = os.getenv('GUILD_ID')
MP3_FILE = os.getenv('MP3_FILE')
# Bot变量
bot = Bot(token=BOT_TOKEN)

# 使用常量
DICE_NUM = 3
MAX_TEAM_COUNT = 5

# 使用字典来管理队伍
teams = {
    'blue': {},
    'red': {}
}

def load_accounts():
    with open('accounts.json', 'r', encoding='utf-8') as f:
        return json.load(f)
    
# 加载kook账号和游戏账号的映射
accounts = load_accounts()
kook2lol = {}  # kook id to lol user summoner_id
lol2kook = {}  # lol user summoner_id to kook id
# 初始化映射
for kook_id, kook_info in accounts.items():
    kook2lol[kook_id] = kook_info['summoner_id']
    lol2kook[kook_info['summoner_id']] = kook_id

@bot.command(name='bind')
async def bind(msg: Message, lol_user_name: str):
    global accounts
    print(lol_user_name)
    nickname = lol_user_name.split('#')[0]
    summoner_id = await get_summoner_id(lol_user_name)
    accounts[msg.author.id] = {
        'nickname': nickname,
        'summoner_id': summoner_id
    }
    with open('accounts.json', 'w', encoding='utf-8') as f:
        json.dump(accounts, f, ensure_ascii=False)
    await msg.reply(f"Kook账号 {msg.author.id} 已绑定到 {lol_user_name}")
    await msg.ctx.guild.set_user_nickname(msg.author.id, nickname)
    await msg.reply(f"已将昵称设置为: {nickname}")


# 获取KOOK在线用户的游戏昵称
async def get_online_user_game_info() -> list[str]:
    global accounts, GUILD_ID
    guild = await bot.client.fetch_guild(GUILD_ID)
    online_members = await guild.fetch_user_list()
    lol_user_info = []
    for member in online_members:
        if not member.bot and member.online:
            lol_account = accounts.get(member.id)
            if lol_account:
                lol_user_info.append(lol_account) 
    return lol_user_info

# 根据游戏昵称获取SommonerId
async def get_summoner_id(lol_user_name: str):
    summoner = await connector.getSummonerByName(lol_user_name)
    print(summoner)
    return summoner['summonerId']


# 创建房间
async def create_room():
    await connector.createARAMLobby("Lazarus", "fantastic")
    
# 切换队伍
async def switch_team():
    await connector.switchTeam()

# 邀请玩家加入房间
async def invite_players(summoner_ids: list[str]):
    await connector.inviteSummonersToLobby(summoner_ids)
    

# 邀请所有在线用户加入房间
async def invite_all_online_players():
    lol_accounts = await get_online_user_game_info()
    summoner_ids = [account['summoner_id'] for account in lol_accounts]
    await invite_players(summoner_ids)



@bot.command(name='online')
async def show_online_nicknames(msg: Message):
    lol_accounts = await get_online_user_game_info()
    # 卡片消息
    card = Card(
        Module.Header("KOOK 在线成员"),
    )
    for lol_account in lol_accounts:
        card.append(Module.Section(Element.Text(lol_account['nickname'])))
    await msg.reply(CardMessage(card))


@bot.command(name='ping')
async def ping(msg: Message):
    await msg.reply("Pong!")

    
@bot.command(name='nickname')
async def set_nickname(msg: Message, nickname: str):
    guild = msg.ctx.guild
    await guild.set_user_nickname(msg.author.id, nickname)
    await msg.reply(f"已将昵称设置为: {nickname}")


async def start(msg: Message, blue_members: list, red_members: list):
    global DICE_NUM
    
    # 获取两个队伍中的所有非bot用户
    participants = blue_members + red_members

    if not participants:
        await msg.reply("红色方和蓝色方队伍中没有非bot用户。")
        return

    # 给每人分配不重复的英雄
    champion_manager.set_participants(participants)
    champion_manager.assign_champions(DICE_NUM)

    # 创建卡片
    red_card = champion_manager.create_team_card("红色方", red_members)
    blue_card = champion_manager.create_team_card("蓝色方", blue_members)

    # 修改创建总体分配情况的卡片部分
    def get_display_name(member):
        return f"`{member.nickname or member.username}`"

    overall_card = Card(
        Module.Header("大乱斗对局"),
        Module.Section(Element.Text("召唤师组队完成，详细信息如下：")),
        Module.Divider(),
        Module.Header("蓝色方"),
        Module.Section(Element.Text(", ".join([get_display_name(member) for member in blue_members]))),
        Module.Divider(),
        Module.Header("红色方"),
        Module.Section(Element.Text(", ".join([get_display_name(member) for member in red_members]))),
    )

    # 在指定频道发送卡片消息
    global TEXT_CHANNEL_ID
    target_channel = await bot.client.fetch_public_channel(TEXT_CHANNEL_ID)
    
    if target_channel:
        # 发送总体分配情况的卡片
        await target_channel.send(CardMessage(overall_card))

        # 为蓝队每个成员发送蓝队卡片
        for user in blue_members:
            await target_channel.send(blue_card, temp_target_id=str(user.id))

        # 为红队每个成员发送红队卡片
        for user in red_members:
            await target_channel.send(red_card, temp_target_id=str(user.id))
        
        # 在发送完消息后清除队伍
        game_start_card = Card(
            Module.Header("比赛开始！"),
        )
        await target_channel.send(CardMessage(game_start_card))
        await target_channel.send('----------------------------------')
        await connector.startChampionSelect()

    else:
        await msg.reply("无法找到指定的频道来发送英雄分配信息。")

@bot.command(name='create')
async def create_command(msg: Message):
    await create_room()
    # 0.5的概率切换队伍
    if random.random() < 0.5:
        await switch_team()
    await invite_all_online_players()

@bot.command(name='start')
async def start_command(msg: Message):
    members = await connector.getLobbyMembers()
    if isinstance(members, dict) and members.get('errorCode') and members.get('message') == 'LOBBY_NOT_FOUND':
        await msg.reply(f"当前没有英雄联盟自定义房间，请输入/create创建房间。")
        return
    blue_members = []
    red_members = []
    for member in members:
        kook_id = lol2kook.get(member['summonerId'])
        if kook_id:
            user = await msg.ctx.guild.fetch_user(kook_id)
            if member['teamId'] == 100:
                blue_members.append(user)
            elif member['teamId'] == 200:
                red_members.append(user)

    await start(msg, blue_members, red_members)
    await play_audio_in_channel(BOT_TOKEN, VOICE_CHANNEL_ID, MP3_FILE)


@bot.on_event(EventTypes.MESSAGE_BTN_CLICK)
async def on_message_btn_click(bot: Bot, event: Event):
    pass


@bot.command(name='joinVoice')
async def join_voice(msg: Message):
    await join_voice_channel(BOT_TOKEN, VOICE_CHANNEL_ID)

@bot.command(name='leaveVoice')
async def leave_voice(msg: Message):
    await leave_voice_channel(BOT_TOKEN, VOICE_CHANNEL_ID)

@bot.command(name='championSelect')
async def champion_select(msg: Message):
    await play_audio_in_channel(BOT_TOKEN, VOICE_CHANNEL_ID, MP3_FILE)


async def main():
    await connector.autoStart()
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
