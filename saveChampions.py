import json

# 打开并读取 JSON 文件
with open('champions.json', 'r', encoding='utf-8') as file:
    champions_data = json.load(file)

print(champions_data['data']['Aatrox']['name'])

champions = []
for champion in champions_data['data']:
    champions.append({
        'name': champions_data['data'][champion]['name'], 
        'title': champions_data['data'][champion]['title'], 
        'en_name': champion,
        'image': f"https://ddragon.leagueoflegends.com/cdn/14.19.1/img/champion/{champion}.png"
    })
    
print(champions)

# 将处理后的英雄列表保存为新的 JSON 文件
with open('lol_champions.json', 'w', encoding='utf-8') as outfile:
    json.dump(champions, outfile, ensure_ascii=False, indent=2)

print("英雄数据已保存到 lol_champions.json 文件中")

