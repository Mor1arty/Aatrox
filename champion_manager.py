import random
import json
import logging
from khl.card import CardMessage, Card, Module, Element, Types
from functools import lru_cache

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

@lru_cache(maxsize=1)
def load_champion_list(file_path="lol_champions.json"):
    logging.info(f"正在从 {file_path} 加载英雄列表")
    with open(file_path, "r", encoding='utf-8') as file:
        champions = json.load(file)
    logging.info(f"成功加载了 {len(champions)} 个英雄")
    return champions

LOL_CHAMPIONS = load_champion_list()

class ChampionManager:
    def __init__(self, champions):
        self.all_champions = champions.copy()
        self.reset_pool()
        self.member_champion_pairs = {}
        self.participants = []

    def reset_pool(self):
        self.available_champions = self.all_champions.copy()
        random.shuffle(self.available_champions)

    def get_champions(self, num_champions):
        if len(self.available_champions) < num_champions:
            self.reset_pool()
        assigned = self.available_champions[:num_champions]
        self.available_champions = self.available_champions[num_champions:]
        return assigned
    
    def set_participants(self, participants):
        self.participants = participants
        
    def assign_champions(self, num_champions):
        self.member_champion_pairs = {
            participant: self.get_champions(num_champions)
            for participant in self.participants
        }
        return self.member_champion_pairs

    def create_team_card(self, team_name, team_members):
        card = Card(
            Module.Header(f"随机英雄分配"),
            Module.Section(Element.Text(f"{team_name}参赛选手的英雄分配如下："))
        )
        for i, member in enumerate(team_members):
            all_champions = self.member_champion_pairs[member]
            image_group = Module.ImageGroup(*[
                Element.Image(champion['image'], alt=champion['en_name'])
                for champion in all_champions
            ])
            
            champion_text = ', '.join(champion['name'] for champion in all_champions)
            display_name = member.nickname or member.username
            card.append(Module.Section(
                Element.Text(f"`{display_name}`: {champion_text}"),
                mode=Types.SectionMode.RIGHT,
                accessory=Element.Button("充值", Types.Click.RETURN_VAL)
            ))
            card.append(image_group)
            if len(team_members) > 1 and i != len(team_members) - 1:
                card.append(Module.Divider())

        return CardMessage(card)

champion_manager = ChampionManager(LOL_CHAMPIONS)