import aiohttp
from async_lru import alru_cache

from app.lol.connector import connector

TAG = "opgg"


class Opgg:
    def __init__(self):
        self.session = None

    async def start(self):
        self.session = aiohttp.ClientSession("https://lol-api-champion.op.gg")

    async def close(self):
        if self.session:
            await self.session.close()

    @alru_cache(maxsize=512)
    async def __fetchTierList(self, region, mode, tier):
        url = f"/api/{region}/champions/{mode}"
        params = {"tier": tier}

        return await self.__get(url, params)

    @alru_cache(maxsize=512)
    async def __fetchChampionBuild(self, region, mode, championId, position, tier):
        if mode != 'arena':
            url = f"/api/{region}/champions/{mode}/{championId}/{position}"
        else:
            url = f"/api/{region}/champions/{mode}/{championId}"

        params = {"tier": tier}

        return await self.__get(url, params)

    @alru_cache(maxsize=512)
    async def getChampionBuild(self, region, mode, championId, position, tier):
        positions = await self.getChampionPositions(region, championId, tier)
        if position not in positions and mode == 'ranked':
            position = positions[0]

        raw = await self.__fetchChampionBuild(region, mode, championId, position, tier)

        if mode != 'arena':
            res = await OpggDataParser.parseOtherChampionBuild(raw, position)
        else:
            res = await OpggDataParser.parseArenaChampionBuild(raw)

        return {
            'data': res,
            'version': raw['meta']['version'],
            'mode': mode,
        }

    @alru_cache(maxsize=512)
    async def getTierList(self, region, mode, tier):
        raw = await self.__fetchTierList(region, mode, tier)

        version = raw['meta']['version']

        if mode == 'ranked':
            res = await OpggDataParser.parseRankedTierList(raw)
        else:
            res = await OpggDataParser.parseOtherTierList(raw)

        return {
            'data': res,
            'version': version
        }

    @alru_cache(maxsize=512)
    async def getChampionPositions(self, region, championId, tier):
        # 这个调用因为有 cache，所以还是挺快的
        data = await self.__fetchTierList(region, "ranked", tier)

        for item in data['data']:
            if item['id'] == championId:
                return [p['name'] for p in item['positions']]

        return []

    async def __get(self, url, params=None):
        res = await self.session.get(url, params=params, ssl=False, proxy=None)
        return await res.json()


class OpggDataParser:

    @staticmethod
    async def parseRankedTierList(data):
        '''
        召唤师峡谷模式下的原始梯队数据，是所有英雄所有位置一起返回的

        在此函数内按照分路位置将它们分开
        '''

        data = data['data']
        res = {p: []
               for p in ['TOP', 'JUNGLE', 'MID', 'ADC', 'SUPPORT']}

        for item in data:
            championId = item['id']
            name = connector.manager.getChampionNameById(championId)
            icon = await connector.getChampionIcon(championId)

            for p in item['positions']:
                position = p['name']

                stats = p['stats']
                tier = stats['tier_data']

                counters = [{
                    'championId': c['champion_id'],
                    'icon': await connector.getChampionIcon(c['champion_id'])
                } for c in p['counters']]

                res[position].append({
                    'championId': championId,
                    'name': name,
                    'icon': icon,
                    'winRate': stats.get('win_rate'),
                    'pickRate': stats.get('pick_rate'),
                    'banRate': stats.get('ban_rate'),
                    'kda': stats.get('kda'),
                    'tier': tier.get('tier'),
                    'rank': tier.get('rank'),
                    'position': position,
                    'counters': counters,
                })

        # 排名 / 梯队是乱的，所以排个序
        for tier in res.values():
            tier.sort(key=lambda x: x['rank'])

        return res

    @staticmethod
    async def parseOtherTierList(data):
        '''
        处理其他模式下的原始梯队数据
        '''

        data = data['data']
        res = []

        for item in data:
            stats = item['average_stats']

            if stats == None:
                continue

            if stats.get('rank') == None:
                continue

            championId = item['id']
            name = connector.manager.getChampionNameById(championId)
            icon = await connector.getChampionIcon(championId)

            res.append({
                'championId': championId,
                'name': name,
                'icon': icon,
                'winRate': stats.get('win_rate'),
                'pickRate': stats.get('pick_rate'),
                'banRate': stats.get('ban_rate'),
                'kda': stats.get('kda'),
                'tier': stats.get('tier'),
                'rank': stats.get('rank'),
                "position": None,
                'counters': [],
            })

        return sorted(res, key=lambda x: x['rank'])

    @staticmethod
    async def parseOtherChampionBuild(data, position):
        data = data['data']

        summary = data['summary']
        championId = summary['id']
        icon = await connector.getChampionIcon(championId)
        name = connector.manager.getChampionNameById(championId)

        if position != 'none':
            for p in summary['positions']:
                if p['name'] == position:
                    stats: dict = p['stats']
                    break

            winRate = stats.get('win_rate')
            pickRate = stats.get('pick_rate')
            banRate = stats.get('ban_rate')
            kda = stats.get('kda')

            tierData: dict = stats['tier_data']
            tier = tierData.get("tier")
            rank = tierData.get("rank")

        else:
            stats = summary['average_stats']
            winRate = stats.get('win_rate')
            pickRate = stats.get('pick_rate')
            banRate = stats.get('ban_rate')
            kda = stats.get('kda')
            tier = stats.get("tier")
            rank = stats.get("rank")

        summonerSpells = []
        for s in data['summoner_spells']:
            icons = [await connector.getSummonerSpellIcon(id)
                     for id in s['ids']]

            summonerSpells.append({
                'ids': s['ids'],
                'icons': icons,
                'win': s['win'],
                'play': s['play'],
                'pickRate': s['pick_rate']
            })

        skills = {
            "masteries": data['skill_masteries'][0]['ids'],
            "order": data['skills'][0]['order'],
            'play': data['skills'][0]['play'],
            'win': data['skills'][0]['win'],
            'pickRate': data['skills'][0]['pick_rate']
        }

        boots = []
        for i in data['boots'][:3]:
            icons = [await connector.getItemIcon(id) for id in i['ids']]
            boots.append({
                "icons": icons,
                "play": i['play'],
                "win": i['win'],
                'pickRate': i['pick_rate']
            })

        startItems = []
        for i in data['starter_items'][:3]:
            icons = [await connector.getItemIcon(id) for id in i['ids']]
            startItems.append({
                "icons": icons,
                "play": i['play'],
                "win": i['win'],
                'pickRate': i['pick_rate']
            })

        coreItems = []
        for i in data['core_items'][:5]:
            icons = [await connector.getItemIcon(id) for id in i['ids']]
            coreItems.append({
                "icons": icons,
                "play": i['play'],
                "win": i['win'],
                'pickRate': i['pick_rate']
            })

        lastItems = []
        for i in data['last_items'][:16]:
            lastItems.append(await connector.getItemIcon(i['ids'][0]))

        strongAgainst = []
        weakAgainst = []

        for c in data['counters']:
            winRate = c['win'] / c['play']
            arr = strongAgainst if winRate >= 0.5 else weakAgainst

            arr.append({
                'championId': (id := c['champion_id']),
                'name': connector.manager.getChampionNameById(id),
                'icon': await connector.getChampionIcon(id),
                'play': c['play'],
                'win': c['win'],
                'winRate': winRate
            })

        strongAgainst.sort(key=lambda x: -x['winRate'])
        weakAgainst.sort(key=lambda x: x['winRate'])

        perks = [{
            'primaryId': (mainId := perk['primary_page_id']),
            "primaryIcon": await connector.getRuneIcon(mainId),
            'secondaryId': (subId := perk['secondary_page_id']),
            "secondaryIcon": await connector.getRuneIcon(subId),
            'perks': (perkIds := perk['primary_rune_ids']+perk['secondary_rune_ids']+perk['stat_mod_ids']),
            "icons": [await connector.getRuneIcon(id) for id in perkIds],
            'play': perk['play'],
            'win': perk['win'],
            'pickRate': perk['pick_rate'],
        } for perk in data['runes']
        ]

        return {
            "summary": {
                'name': name,
                'championId': championId,
                'icon': icon,
                'position': position,
                'winRate': winRate,
                'pickRate': pickRate,
                'banRate': banRate,
                'kda': kda,
                'tier': tier,
                'rank': rank
            },
            "summonerSpells": summonerSpells,
            "championSkills": skills,
            "items": {
                "boots": boots,
                "startItems": startItems,
                "coreItems": coreItems,
                "lastItems": lastItems,
            },
            "counters": {
                "strongAgainst": strongAgainst,
                "weakAgainst": weakAgainst,
            },
            "perks": perks,
        }

    @staticmethod
    async def parseArenaChampionBuild(data):
        data = data['data']

        summary = data['summary']
        championId = summary['id']
        name = connector.manager.getChampionNameById(championId)
        icon = await connector.getChampionIcon(championId)

        stats = summary['average_stats']
        play = stats['play']
        winRate = stats['win'] / play
        firstRate = stats['first_place'] / play
        averagePlace = stats['total_place'] / play
        pickRate = stats['pick_rate']
        banRate = stats['ban_rate']
        tier = stats['tier']

        skills = {
            "masteries": data['skill_masteries'][0]['ids'],
            "order": data['skills'][0]['order'],
            'play': data['skills'][0]['play'],
            'win': data['skills'][0]['win'],
            'pickRate': data['skills'][0]['pick_rate']
        }

        boots = []
        for i in data['boots'][:3]:
            icons = [await connector.getItemIcon(id) for id in i['ids']]
            boots.append({
                "icons": icons,
                "play": i['play'],
                "win": i['win'],
                'pickRate': i['pick_rate'],
                "averatePlace": i['total_place'] / i['play'],
                "firstRate": i['first_place'] / i['play']
            })

        startItems = []
        for i in data['starter_items'][:3]:
            icons = [await connector.getItemIcon(id) for id in i['ids']]
            startItems.append({
                "icons": icons,
                "play": i['play'],
                "win": i['win'],
                'pickRate': i['pick_rate'],
                "averatePlace": i['total_place'] / i['play'],
                "firstRate": i['first_place'] / i['play']
            })

        coreItems = []
        for i in data['core_items'][:5]:
            icons = [await connector.getItemIcon(id) for id in i['ids']]
            coreItems.append({
                "icons": icons,
                "play": i['play'],
                "win": i['win'],
                'pickRate': i['pick_rate'],
                "averatePlace": i['total_place'] / i['play'],
                "firstRate": i['first_place'] / i['play']
            })

        lastItems = []
        for i in data['last_items'][:16]:
            lastItems.append(await connector.getItemIcon(i['ids'][0]))

        augments = []
        for item in data['augment_group']:
            arr = [{
                "id": (augId := aug['id']),
                "icon": await connector.getAugmentIcon(augId),
                "name": connector.manager.getAugmentsName(augId),
                "win": aug['win'],
                'play': aug['play'],
                "totalPlace": aug['total_place'],
                "firstPlace": aug['first_place'],
                'pickRate': aug['pick_rate']
            }for aug in item['augments']]

            augments.append(arr)

        synergies = [{
            "championId": (chId := syn['champion_id']),
            'icon': await connector.getChampionIcon(chId),
            "name": connector.manager.getChampionNameById(chId),
            "win": syn['win'],
            'play': syn['play'],
            "totalPlace": syn['total_place'],
            "firstPlace": syn['first_place'],
            'pickRate': syn['pick_rate']
        }for syn in data['synergies']]

        return {
            "summary": {
                "name": name,
                "icon": icon,
                "championId": championId,
                "play": play,
                "winRate": winRate,
                "firstRate": firstRate,
                "averagePlace": averagePlace,
                "pickRate": pickRate,
                "banRate": banRate,
                "tier": tier,
                "position": "none"
            },
            "championSkills": skills,
            "items": {
                "boots": boots,
                "startItems": startItems,
                "coreItems": coreItems,
                "lastItems": lastItems,
            },
            "augments": augments,
            "synergies": synergies,
        }


opgg = Opgg()
