"""Mandarin radiotelephony (the CAAC's): "国航幺两三，上升到高度层幺两洞保持"; digits as the radio says them (幺 1,
两 2, 拐 7, 洞 0)."""

import re

from localtc.atc_core.phraseology.tongues import Tongue

DIGITS = {"洞": 0, "零": 0, "〇": 0, "幺": 1, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "拐": 7,
          "七": 7, "八": 8, "九": 9, "勾": 9}
UNITS = {"十": 10, "百": 100, "千": 1000}
NUMERAL = re.compile("[" + "".join(DIGITS) + "".join(UNITS) + "万]+")

# Words with a numeral in them that aren't numbers.
NOT_NUMBERS = (("一边航向", "跑道航向"), ("等一下", "稍等"), ("一下", ""), ("一些", ""), ("一点", ""),
               ("进一步", "进壹步"), ("三边", "顺风边"), ("四边", "基线边"), ("五边", "最后进近"))


def _value(run: str) -> str:
    if not any(c in UNITS or c == "万" for c in run):
        return "".join(str(DIGITS[c]) for c in run)  # one by one: 幺两洞 is 120
    total = section = number = 0
    for c in run:
        if c in DIGITS:
            number = DIGITS[c]
        elif c in UNITS:
            section += (number or 1) * UNITS[c]
            number = 0
        elif c == "万":
            total += (section + number) * 10000
            section = number = 0
    return str(total + section + number)


def figures(text: str) -> str:
    """Chinese numerals as figures ("高度层幺两洞" -> "高度层120", "三千五百英尺" -> "3500英尺"), a runway's side after
    its number in English ("三六左" -> "36 left")."""
    for words, instead in NOT_NUMBERS:
        text = text.replace(words, instead)
    text = NUMERAL.sub(lambda m: " " + _value(m.group(0)) + " ", text)
    text = re.sub(r"(\d)\s*([左右中])(?!转)", lambda m: m.group(1) + {"左": " left ", "右": " right ", "中": " center "}[m.group(2)], text)
    return text


# What a Chinese pilot or controller says, put into English for the grammar: the longest first, anywhere (there are
# no spaces between words), after the numbers are figures.
LEXICON = (
    (r"fl(?= ?\d)", " flight level "),
    (r"复诵正确", " readback correct "),
    (r"请复诵", " read back "),
    (r"(?:可以)?(?:按计划)?(?:飞往|飞向|前往)", " cleared to "),
    (r"按(?:飞行)?计划", " as filed "),
    (r"离场频率", " departure frequency "),
    (r"起飞后\s*(\d+)\s*分钟|(\d+)\s*分钟后", r" \1\2 minutes after departure "),
    (r"预计", " expect "),
    # the runway
    (r"进跑道(?:并)?等待|进入跑道等待", " line up and wait "),
    (r"可以起飞|起飞许可", " cleared for takeoff "),
    (r"可以(?:落地|着陆)|落地许可|着陆许可", " cleared to land "),
    (r"可以(?:连续起落|接地复飞|触地复飞)", " cleared touch and go "),
    (r"中断起飞", " rejected takeoff "),
    (r"复飞", " going around "),
    (r"立即停止", " stop immediately "),
    (r"取消起飞(?:许可)?", " cancel takeoff clearance "),
    (r"(?:保持)?(?:跑道|一边)航向", " runway heading "),
    (r"原地等待|保持位置|就地等待", " hold position "),
    (r"跑道\s*(\d{1,2}[lrc]?)\s*外等待", r" hold short runway \1 "),
    (r"外等待", " hold short "),
    (r"在\s*([a-z0-9 ]+?)\s*(?:上空)?等待", r" hold at \1 "),
    (r"退出等待", " leave the hold "),
    (r"预计进壹步许可(?:时间)?|进壹步许可", " expect further clearance "),
    (r"在落地飞机后面|落地飞机后面", " behind the landing traffic "),
    (r"后面", " behind "),
    (r"飞越", " cross "),
    (r"加快|不要耽误", " expediting "),
    (r"航线起落|航线", " closed traffic "),
    (r"顺风边", " downwind "),
    (r"基线边", " base "),
    (r"等待点|等待位置", " holding point "),
    (r"左转弯|左转", " turn left "),
    (r"右转弯|右转", " turn right "),
    # the ground
    (r"推出(?:并)?开车|推出和开车", " push and start "),
    (r"推出", " pushback "),
    (r"开车", " startup "),
    (r"准备好(?:了)?起飞|准备起飞", " ready for departure "),
    (r"准备好(?:了)?滑行|准备滑行", " ready to taxi "),
    (r"准备好(?:了)?", " ready "),
    (r"滑行到|滑到", " taxi to "),
    (r"滑行", " taxi "),
    (r"经由|沿着|沿|经", " via "),
    (r"穿越", " cross "),
    (r"脱离(?:跑道)?", " vacate "),
    (r"停机位|机坪", " parking "),
    (r"通用航空", " general aviation "),
    (r"自行决定|自己掌握", " at your discretion "),
    (r"同意|批准", " approved "),
    (r"跟随|跟着", " follow "),
    (r"避让", " give way to "),
    # levels, headings, speeds
    (r"上升中|正在上升", " climbing "),
    (r"下降中|正在下降", " descending "),
    (r"继续上升", " continue climb "),
    (r"继续下降", " continue descent "),
    (r"上升(?:到|至)?|上高度", " climb "),
    (r"下降(?:到|至)?|下高度", " descend "),
    (r"保持(?:现在|当前)航向", " present heading "),
    (r"保持", " maintain "),
    (r"(?:飞行)?高度层", " flight level "),
    (r"修正海压|修正海平面气压", " qnh "),
    (r"英尺", " feet "),
    (r"航向", " heading "),
    (r"减速(?:到|至)?|减小速度(?:到|至)?", " reduce speed "),
    (r"恢复正常速度|速度自行调整", " resume normal speed "),
    (r"速度", " speed "),
    (r"(?:到)?最后进近\s*(\d+)\s*海里|(\d+)\s*海里最后进近", r" until \1\2 mile final "),
    (r"海里", " miles "),
    (r"节", " knots "),
    (r"度", " degrees "),
    # the approach
    (r"可以目视进近|目视进近", " cleared visual approach "),
    (r"目视", " visual "),
    (r"可以", " cleared "),
    (r"进近", " approach "),
    (r"建立(?:航向道)?", " established "),
    (r"航向道|航向信标", " localizer "),
    (r"切入", " join "),
    (r"雷达引导", " vectors for "),
    (r"最后进近", " final "),
    (r"看到跑道|跑道在视", " runway in sight "),
    (r"看到(?:活动|飞机)", " traffic in sight "),
    (r"改到跑道", " side-step runway "),
    (r"(左|右)转等待", r" \1 turns "),
    # the radio
    (r"联系", " contact "),
    (r"频率", " frequency "),
    (r"应答机(?:编码)?|编码|squawk", " squawk "),
    (r"点", " decimal "),
    (r"雷达已识别|雷达识别|雷达看到", " radar contact "),
    (r"请重复|再说一遍|重复一遍", " say again "),
    (r"我重复", " i say again "),
    (r"无线电检查|听我几个", " radio check "),
    (r"(?:听你)?\s*5\s*个|声音清楚", " five by five "),
    (r"是的|肯定|对的", " affirm "),
    (r"否定|不对|不是", " negative "),
    (r"收到|明白|知道了", " roger "),
    (r"稍等|请等待", " stand by "),
    (r"再见|一路平安", " good day "),
    (r"你好|您好", " hello "),
    (r"谢谢", " thanks "),
    (r"通过|穿过", " passing "),
    (r"离开", " leaving "),
    (r"请求|申请", " request "),
    (r"更高", " higher "),
    (r"更低", " lower "),
    (r"直飞", " direct "),
    (r"继续", " continue "),
    (r"报告", " report "),
    (r"通波|情报|atis", " information "),
    (r"静风", " calm "),
    (r"风", " wind "),
    (r"活动|有飞机", " traffic "),
    (r"紧急情况|紧急", " emergency "),
    (r"离场程序|离场", " departure "),
    (r"进场程序|进场", " arrival "),
    (r"然后|之后", " then "),
    (r"塔台", " tower "),
    (r"地面", " ground "),
    (r"放行", " clearance "),
    (r"区调|区域|管制", " control "),
    (r"跑道", " runway "),
    (r"左", " left "),
    (r"右", " right "),
    (r"中", " center "),
    (r"机位|登机口", " gate "),
    (r"北", " north "),
    (r"南", " south "),
    (r"东", " east "),
    (r"西", " west "),
    (r"[，、]", ", "),
    (r"。", ". "),
    (r"号|的|到|在|我们|您|你|请|了", " "),
)

TONGUE = Tongue(
    code="zh",
    digits=("洞", "幺", "两", "三", "四", "五", "六", "拐", "八", "九"),
    point="点",
    sides={"L": "左", "R": "右", "C": "中"},
    flight_level="高度层",
    feet="英尺",
    knots="节",
    degrees="度",
    calm="静风",
    runway="跑道",
    visual="目视",
    sep="",
    letters={c: c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    words={"left": "左", "right": "右", "northbound": "向北", "southbound": "向南", "eastbound": "向东",
           "westbound": "向西", "straight-out": "直线", "north": "北", "south": "南", "east": "东", "west": "西",
           "northeast": "东北", "northwest": "西北", "southeast": "东南", "southwest": "西南",
           "southwestbound": "向西南", "northwestbound": "向西北", "southeastbound": "向东南", "northeastbound": "向东北"},
    stations={"Tower": "塔台", "Ground": "地面", "Approach": "进近", "Departure": "离场", "Center": "区调",
              "Centre": "区调", "Control": "区调", "Clearance": "放行", "Delivery": "放行", "Clearance Delivery": "放行",
              "Radio": "无线电", "Radar": "雷达", "Information": "情报", "Apron": "机坪", "Arrival": "进场",
              "Director": "引导"},
    places={"gate": "机位", "stand": "机位", "parking": "停机位", "ramp": "机坪", "spot": "机位"},
    wind="{direction}{degrees}{speed}{knots}",
    level_spoken="{flight_level}{digits}",
    feet_spoken="{n}{feet}",
    lexicon=LEXICON,
    pilot={
        "with_you": "{station}, {callsign}, 你好",
        "level": "{station}, {callsign}, 你好, 保持{level}",
        "climbing": "{station}, {callsign}, 你好, 通过{level}上升到{cleared}",
        "descending": "{station}, {callsign}, 你好, 通过{level}下降到{cleared}",
        "descending_only": "{station}, {callsign}, 你好, 通过{level}下降",
        "descending_via": "{station}, {callsign}, 你好, 通过{level}按{procedure}进场下降",
        "information": ", 有通波{atis}",
        "going_around_level": "{station}, {callsign}, 复飞, 通过{level}",
        "going_around": "{station}, {callsign}, 复飞",
        "final": "{station}, {callsign}, 五边{miles}海里跑道{runway}",
        "inbound": "{station}, {callsign}, 进近跑道{runway}",
        "vacated": "{station}, {callsign}, 已脱离跑道{runway}, 请求滑行到停机位",
        "vacated_no_runway": "{station}, {callsign}, 已脱离跑道, 请求滑行到停机位",
        "taxi": "{station}, {callsign}, {where}请求滑行{information}",
        "pushback": "{station}, {callsign}, {where}请求推出开车{information}",
        "ready": "{station}, {callsign}, 跑道{runway}外等待, 准备好起飞",
        "ready_no_runway": "{station}, {callsign}, 准备好起飞",
        "ifr": "{station}, {callsign}, {where}请求放行到{destination}{information}",
        "looking": "正在看, {callsign}",
        "loud_and_clear": "听你五个, {callsign}",
        "mayday": "Mayday, Mayday, Mayday, {station}, {callsign}, 遇险",
        "pan": "Pan Pan, Pan Pan, Pan Pan, {station}, {callsign}, 紧急情况",
    },
)
