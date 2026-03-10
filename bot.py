import discord
from discord.ext import commands
import aiohttp
import random
import traceback
import os  # 보안을 위해 환경 변수를 불러오는 모듈입니다.

# ================= [ 설정 구역 ] =================
# 깃허브 Secrets에 저장한 이름을 그대로 가져옵니다.
# 코드를 공개(Public)로 전환해도 실제 키값은 노출되지 않습니다.
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
TOKEN = os.getenv("DISCORD_TOKEN")

# 티어 목록 (기존 역할 제거 및 대조용)
TIER_DATA = {
    "Challenger": 0xf4c874, "Grandmaster": 0xc64444, "Master": 0x9d5ca3,
    "Diamond": 0x576bce, "Emerald": 0x2da161, "Platinum": 0x4e9996,
    "Gold": 0xcd8837, "Silver": 0x80989d, "Bronze": 0x8c513a,
    "Iron": 0x51484a, "Unranked": 0x000000
}
TIER_LIST = list(TIER_DATA.keys())
# ===============================================

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)
pending_users = {} # 임시 인증 대기 명단

@bot.event
async def on_ready():
    print(f'--- 봇 로그인 성공: {bot.user.name} ---')

# 1. 봇이 서버에 초대되었을 때 역할 자동 생성
@bot.event
async def on_guild_join(guild):
    print(f"새로운 서버 입장: {guild.name}")
    for role_name, color_hex in TIER_DATA.items():
        if not discord.utils.get(guild.roles, name=role_name):
            try:
                await guild.create_role(name=role_name, color=discord.Color(color_hex), hoist=True)
            except discord.Forbidden:
                print(f"{guild.name} 서버에서 역할 생성 권한이 없습니다.")
                break

# 2. 인증 시작 명령어
@bot.command()
async def 인증(ctx, *, summoner_name):
    if "#" not in summoner_name:
        await ctx.send("❌ 소환사명 뒤에 태그(#)를 포함해 주세요. (예: 페이커#KR1)")
        return
    
    target_icon = random.randint(0, 28)
    pending_users[ctx.author.id] = {"name": summoner_name, "icon": target_icon}
    
    icon_url = f"https://ddragon.leagueoflegends.com/cdn/14.1.1/img/profileicon/{target_icon}.png"
    embed = discord.Embed(
        title="🛡️ 롤 계정 소유권 인증", 
        description=f"**{summoner_name}**님, 본인 확인을 위해\n프로필 아이콘을 아래 이미지로 변경한 후 `!확인`을 입력하세요.", 
        color=0x5865F2
    )
    embed.set_thumbnail(url=icon_url)
    embed.set_footer(text="인증이 완료되면 다시 원래 아이콘으로 바꾸셔도 됩니다.")
    await ctx.send(embed=embed)

# 3. 아이콘 변경 확인 및 연동 성공 알림
@bot.command()
async def 확인(ctx):
    if ctx.author.id not in pending_users:
        await ctx.send("먼저 `!인증 소환사명#태그`를 입력해 인증을 시작하세요.")
        return

    user_info = pending_users[ctx.author.id]
    name, tag = user_info["name"].split("#")
    
    async with aiohttp.ClientSession() as session:
        try:
            # PUUID 조회
            acc_url = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}?api_key={RIOT_API_KEY}"
            async with session.get(acc_url) as r1:
                acc_data = await r1.json()
                puuid = acc_data.get('puuid')
                if not puuid:
                    await ctx.send("❌ 라이엇 계정 정보를 찾을 수 없습니다.")
                    return

            # 현재 아이콘 조회
            sum_url = f"https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}?api_key={RIOT_API_KEY}"
            async with session.get(sum_url) as r2:
                sum_data = await r2.json()
                current_icon = sum_data.get('profileIconId')

            if current_icon == user_info["icon"]:
                await ctx.send(f"✅ **{user_info['name']}**님, 인증에 성공했습니다!\n이제 `!갱신 {user_info['name']}`을 입력하여 티어 역할을 받으세요.")
                del pending_users[ctx.author.id]
            else:
                await ctx.send(f"❌ 아이콘이 다릅니다. (현재: {current_icon}번 / 목표: {user_info['icon']}번)\n아이콘 변경 후 다시 `!확인`을 눌러주세요.")
        
        except Exception as e:
            traceback.print_exc()
            await ctx.send("오류가 발생했습니다. 라이엇 API 키를 확인하세요.")

# 4. 티어 실시간 갱신 (무저장 방식)
@bot.command()
async def 갱신(ctx, *, summoner_name):
    if "#" not in summoner_name:
        await ctx.send("❌ `!갱신 소환사명#태그` 형식으로 입력해주세요.")
        return

    name, tag = summoner_name.split("#")

    async with aiohttp.ClientSession() as session:
        try:
            acc_url = f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}?api_key={RIOT_API_KEY}"
            async with session.get(acc_url) as r1:
                acc_data = await r1.json()
                puuid = acc_data.get('puuid')
                if not puuid:
                    await ctx.send("❌ 해당 소환사 정보를 찾을 수 없습니다.")
                    return

            league_url = f"https://kr.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}?api_key={RIOT_API_KEY}"
            async with session.get(league_url) as r2:
                league_data = await r2.json()
                
                user_tier = "UNRANKED"
                for entry in league_data:
                    if entry['queueType'] == 'RANKED_SOLO_5x5':
                        user_tier = entry['tier']
                        break
                
                role_name = user_tier.capitalize()
                new_role = discord.utils.get(ctx.guild.roles, name=role_name)

                if not new_
