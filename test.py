import asyncio
from app.lol.connector import connector

async def main():
    try:
        await connector.autoStart()
        await connector.createARAMLobby("Lazarus", "fantastic")
        await connector.switchTeam()
        print('-'*20)
        members = await connector.getLobbyMembers()
        for member in members:
            print(member['summonerId'])
            print(member['summonerName'])
            print(member['teamId'])
        # members = [member['summonerId'] for member in await connector.getLobbyMembers()]
        # print(await connector.getSummonerById(members[0]))
        user_1 = await connector.getSummonerByName("一朵小吹口呀#34543")
        summoner_id_1 = user_1['summonerId']
        user_2 = await connector.getSummonerByName("丁 真#70412")
        summoner_id_2 = user_2['summonerId']
        await connector.inviteSummonersToLobby([summoner_id_1, summoner_id_2])
        # await asyncio.sleep(3)
        
        # await connector.startChampionSelect()
        
    except Exception as e:
        print(f"error: {e}")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
