# bot.py - Complete Bot with /check, /forward, /map commands
import os
import re
import asyncio
from collections import OrderedDict
from telethon import TelegramClient, events
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== CONFIGURATION =====
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH', '')
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
# ===== END CONFIGURATION =====

bot = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

user_states = {}


class UserState:
    def __init__(self):
        self.command = None          # 'check' or 'forward' or 'map'
        self.step = None
        self.txt_file_opids = OrderedDict()  # opid -> {line_number, full_line}
        self.txt_opid_order = []     # ordered list of opids from txt file
        self.txt_file_lines = []     # all lines with metadata
        self.txt_raw_lines = []      # raw lines as-is from file
        self.channel_id = None
        self.channel_id_short = None # short form like 249495393
        self.start_msg_id = None
        self.end_msg_id = None
        self.target_channel_id = None


# ===== UTILITY FUNCTIONS =====

def extract_opid_from_txt_line(line):
    """
    Extract OPID from txt line format: 🤡HEX(BASE64)🤡
    Returns the full OPID string: hex(base64)
    """
    pattern = r'🤡([a-f0-9]+\([A-Za-z0-9+/=]+\))🤡'
    match = re.search(pattern, line)
    return match.group(1) if match else None


def extract_opid_from_caption(caption):
    """
    Extract OPID from channel message caption: OPID >> HEX(BASE64)
    Returns the full OPID string: hex(base64)
    """
    pattern = r'OPID\s*>>\s*([a-f0-9]+\([A-Za-z0-9+/=]+\))'
    match = re.search(pattern, caption)
    return match.group(1) if match else None


def parse_channel_link(link):
    """
    Parse https://t.me/c/CHANNEL_ID/MSG_ID
    Returns (full_channel_id, message_id, short_channel_id)
    """
    pattern = r'https://t\.me/c/(\d+)/(\d+)'
    match = re.search(pattern, link.strip())
    if match:
        short_id = match.group(1)
        channel_id = int(f"-100{short_id}")
        msg_id = int(match.group(2))
        return channel_id, msg_id, short_id
    return None, None, None


def validate_target_channel_id(text):
    """Validate target channel ID format: -100XXXXXXXXXX"""
    text = text.strip()
    try:
        cid = int(text)
        if str(cid).startswith('-100'):
            return cid
    except ValueError:
        pass
    return None


def replace_line_link(line, new_link):
    """
    Replace the URL at the end of a txt line with new_link.
    Lines end with: 🤬SOMETHING🤬 : URL
    We replace everything after the last ' : ' (the URL part).
    """
    # Find the last occurrence of the pattern 🤬...🤬 : URL
    # The URL is everything after the last ' : '
    # But we need to be careful - the line has structure ending with:
    # 🤬Type🤬 : https://...
    
    pattern = r'(🤬[^🤬]*🤬\s*:\s*)(\S+)$'
    match = re.search(pattern, line)
    if match:
        return line[:match.start(2)] + new_link
    
    # Fallback: replace last URL in the line
    url_pattern = r'(https?://\S+)$'
    match = re.search(url_pattern, line.rstrip())
    if match:
        return line[:match.start(1)] + new_link
    
    # If nothing found, just append
    return line


# ===== COMMAND HANDLERS =====

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.respond(
        "👋 **Welcome to OPID Bot!**\n\n"
        "**Commands:**\n"
        "├ /check - Find missing OPIDs between file & channel\n"
        "├ /forward - Forward messages in .txt order to target channel\n"
        "├ /map - Map OPIDs to channel message links\n"
        "└ /cancel - Cancel current operation\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "**📋 /check** - Find missing OPIDs\n"
        "1️⃣ Send .txt file\n"
        "2️⃣ Send source start link\n"
        "3️⃣ Send source end link\n"
        "4️⃣ Bot finds missing OPIDs → sends report\n\n"
        "**📤 /forward** - Forward in order\n"
        "1️⃣ Send .txt file (defines order)\n"
        "2️⃣ Send target channel ID\n"
        "3️⃣ Send source start link\n"
        "4️⃣ Send source end link\n"
        "5️⃣ Bot forwards without forward tag\n\n"
        "**🗺️ /map** - Map OPIDs to links\n"
        "1️⃣ Send .txt file\n"
        "2️⃣ Send source start link\n"
        "3️⃣ Send source end link\n"
        "4️⃣ Bot replaces URLs with channel msg links"
    )


@bot.on(events.NewMessage(pattern='/cancel'))
async def cancel_handler(event):
    uid = event.sender_id
    if uid in user_states:
        del user_states[uid]
    await event.respond("❌ **Operation cancelled.**")


@bot.on(events.NewMessage(pattern='/check'))
async def check_handler(event):
    uid = event.sender_id
    user_states[uid] = UserState()
    user_states[uid].command = 'check'
    user_states[uid].step = 'waiting_txt'
    await event.respond(
        "📄 **[CHECK MODE]**\n\n"
        "Send me the **.txt file** containing OPID data."
    )


@bot.on(events.NewMessage(pattern='/forward'))
async def forward_handler(event):
    uid = event.sender_id
    user_states[uid] = UserState()
    user_states[uid].command = 'forward'
    user_states[uid].step = 'waiting_txt'
    await event.respond(
        "📄 **[FORWARD MODE]**\n\n"
        "Send me the **.txt file** containing OPID data.\n"
        "⚠️ Order of OPIDs in file = order messages will be forwarded."
    )


@bot.on(events.NewMessage(pattern='/map'))
async def map_handler(event):
    uid = event.sender_id
    user_states[uid] = UserState()
    user_states[uid].command = 'map'
    user_states[uid].step = 'waiting_txt'
    await event.respond(
        "🗺️ **[MAP MODE]**\n\n"
        "Send me the **.txt file** containing OPID data.\n"
        "Bot will replace all URLs with channel message links."
    )


# ===== MAIN MESSAGE HANDLER =====

@bot.on(events.NewMessage())
async def handler(event):
    uid = event.sender_id

    if uid not in user_states:
        return

    state = user_states[uid]

    # Ignore commands
    if event.raw_text and event.raw_text.startswith('/'):
        return

    # ========================
    # STEP: RECEIVE TXT FILE
    # ========================
    if state.step == 'waiting_txt':
        if not event.document:
            return

        fname = ""
        for attr in event.document.attributes:
            if hasattr(attr, 'file_name'):
                fname = attr.file_name
                break

        if not fname.endswith('.txt'):
            await event.respond("❌ Send a **.txt** file only!")
            return

        status = await event.respond("⏳ **Parsing file...**")

        data = await bot.download_media(event.message, bytes)
        content = data.decode('utf-8', errors='ignore')
        lines = content.strip().split('\n')

        count = 0
        for num, line in enumerate(lines, 1):
            raw_line = line.rstrip('\r')
            state.txt_raw_lines.append(raw_line)

            stripped = raw_line.strip()
            if not stripped:
                state.txt_file_lines.append({'num': num, 'line': '', 'opid': None})
                continue

            opid = extract_opid_from_txt_line(stripped)
            state.txt_file_lines.append({'num': num, 'line': stripped, 'opid': opid})

            if opid:
                state.txt_file_opids[opid] = {
                    'line_number': num,
                    'full_line': stripped,
                    'raw_index': num - 1  # index in txt_raw_lines
                }
                state.txt_opid_order.append(opid)
                count += 1

        mode_name = {
            'check': 'CHECK',
            'forward': 'FORWARD',
            'map': 'MAP'
        }.get(state.command, '???')

        if state.command == 'check':
            await status.edit(
                f"✅ **[{mode_name}] File parsed!**\n"
                f"📊 Lines: **{len(lines)}** | OPIDs: **{count}**\n\n"
                f"🔗 Send **starting** message link of source channel:\n"
                f"`https://t.me/c/XXXXX/XX`"
            )
            state.step = 'waiting_start'

        elif state.command == 'forward':
            await status.edit(
                f"✅ **[{mode_name}] File parsed!**\n"
                f"📊 Lines: **{len(lines)}** | OPIDs: **{count}**\n\n"
                f"🎯 Send **target channel ID**:\n"
                f"Format: `-100XXXXXXXXXX`"
            )
            state.step = 'waiting_target'

        elif state.command == 'map':
            await status.edit(
                f"✅ **[{mode_name}] File parsed!**\n"
                f"📊 Lines: **{len(lines)}** | OPIDs: **{count}**\n\n"
                f"🔗 Send **starting** message link of source channel:\n"
                f"`https://t.me/c/XXXXX/XX`"
            )
            state.step = 'waiting_start'

        return

    # ======================================
    # STEP: RECEIVE TARGET CHANNEL (FORWARD)
    # ======================================
    if state.step == 'waiting_target':
        target_id = validate_target_channel_id(event.raw_text)
        if not target_id:
            await event.respond(
                "❌ Invalid channel ID!\n"
                "Send like: `-10035835363`\n"
                "Must start with `-100`"
            )
            return

        state.target_channel_id = target_id
        await event.respond(
            f"✅ **Target set:** `{target_id}`\n\n"
            f"🔗 Now send **starting** message link of **source** channel:\n"
            f"`https://t.me/c/XXXXX/XX`"
        )
        state.step = 'waiting_start'
        return

    # ========================
    # STEP: RECEIVE START LINK
    # ========================
    if state.step == 'waiting_start':
        ch_id, msg_id, short_id = parse_channel_link(event.raw_text)
        if not ch_id:
            await event.respond("❌ Invalid! Use: `https://t.me/c/XXXXX/XX`")
            return

        state.channel_id = ch_id
        state.channel_id_short = short_id
        state.start_msg_id = msg_id

        await event.respond(
            f"✅ **Start:** Channel `{ch_id}` | Msg `{msg_id}`\n\n"
            f"🔗 Now send **last** message link:"
        )
        state.step = 'waiting_end'
        return

    # ======================
    # STEP: RECEIVE END LINK
    # ======================
    if state.step == 'waiting_end':
        ch_id, msg_id, short_id = parse_channel_link(event.raw_text)
        if not ch_id:
            await event.respond("❌ Invalid format!")
            return
        if ch_id != state.channel_id:
            await event.respond("❌ Different channel! Must be same as start link.")
            return
        if msg_id <= state.start_msg_id:
            await event.respond("❌ End ID must be greater than start ID!")
            return

        state.end_msg_id = msg_id
        state.step = 'processing'
        total = state.end_msg_id - state.start_msg_id + 1

        if state.command == 'check':
            status = await event.respond(
                f"⏳ **[CHECK] Scanning {total} messages...**"
            )
            await do_check(event, state, status)

        elif state.command == 'forward':
            status = await event.respond(
                f"⏳ **[FORWARD] Phase 1: Scanning {total} messages...**\n"
                f"Building OPID → Message mapping..."
            )
            await do_forward(event, state, status)

        elif state.command == 'map':
            status = await event.respond(
                f"⏳ **[MAP] Scanning {total} messages...**\n"
                f"Building OPID → Message ID mapping..."
            )
            await do_map(event, state, status)

        if uid in user_states:
            del user_states[uid]
        return


# ===== CHECK LOGIC =====

async def do_check(event, state, status_msg):
    """Find missing OPIDs between txt file and channel."""

    channel_opids = set()
    fetched = 0
    empty = 0
    total = state.end_msg_id - state.start_msg_id + 1
    BATCH = 100
    all_ids = list(range(state.start_msg_id, state.end_msg_id + 1))
    last_progress = 0

    try:
        for i in range(0, len(all_ids), BATCH):
            batch = all_ids[i:i + BATCH]

            try:
                msgs = await bot.get_messages(state.channel_id, ids=batch)
            except Exception as e:
                logger.error(f"Batch error: {e}")
                await asyncio.sleep(2)
                try:
                    msgs = await bot.get_messages(state.channel_id, ids=batch)
                except:
                    empty += len(batch)
                    continue

            for msg in msgs:
                if msg is None:
                    empty += 1
                    continue
                fetched += 1
                caption = msg.message or ""
                if caption:
                    opid = extract_opid_from_caption(caption)
                    if opid:
                        channel_opids.add(opid)

            done = fetched + empty
            if done - last_progress >= 300:
                last_progress = done
                pct = (done / total) * 100
                try:
                    await status_msg.edit(
                        f"⏳ **[CHECK] Scanning...**\n"
                        f"Progress: **{done}/{total}** ({pct:.0f}%)\n"
                        f"OPIDs found: **{len(channel_opids)}**"
                    )
                except:
                    pass

            if i % 500 == 0 and i > 0:
                await asyncio.sleep(0.5)

    except Exception as e:
        await event.respond(f"❌ Error: `{e}`")
        return

    txt_set = set(state.txt_file_opids.keys())
    missing = txt_set - channel_opids

    if not missing:
        await status_msg.edit(
            f"✅ **[CHECK] Complete!**\n\n"
            f"Scanned: **{fetched}** msgs\n"
            f"File OPIDs: **{len(txt_set)}**\n"
            f"Channel OPIDs: **{len(channel_opids)}**\n\n"
            f"🎉 **No missing OPIDs!**"
        )
        return

    entries = sorted(
        [(state.txt_file_opids[op]['line_number'], state.txt_file_opids[op]['full_line'])
         for op in missing],
        key=lambda x: x[0]
    )

    output = "\n".join(e[1] for e in entries)
    out_fname = f"missing_{event.sender_id}.txt"

    with open(out_fname, 'w', encoding='utf-8') as f:
        f.write(output)

    line_nums = [str(e[0]) for e in entries]
    nums_display = ", ".join(line_nums[:60])
    if len(line_nums) > 60:
        nums_display += f" ... +{len(line_nums) - 60} more"

    await status_msg.edit(
        f"✅ **[CHECK] Complete!**\n\n"
        f"📊 Scanned: **{fetched}**\n"
        f"🔑 File OPIDs: **{len(txt_set)}**\n"
        f"🔑 Channel OPIDs: **{len(channel_opids)}**\n\n"
        f"❌ **Missing: {len(missing)}**\n"
        f"📍 Lines: {nums_display}"
    )

    await bot.send_file(
        event.chat_id,
        out_fname,
        caption=f"📄 **{len(missing)} Missing OPIDs**\nLines: {nums_display}"
    )

    if os.path.exists(out_fname):
        os.remove(out_fname)


# ===== MAP LOGIC =====

async def do_map(event, state, status_msg):
    """
    Scan source channel, map each OPID to its message ID,
    then replace URLs in .txt lines with https://t.me/c/CHANNEL/MSG_ID
    """

    opid_to_msg_id = {}  # opid -> source message id
    fetched = 0
    empty = 0
    total = state.end_msg_id - state.start_msg_id + 1
    BATCH = 100
    all_ids = list(range(state.start_msg_id, state.end_msg_id + 1))
    last_progress = 0

    try:
        for i in range(0, len(all_ids), BATCH):
            batch = all_ids[i:i + BATCH]

            try:
                msgs = await bot.get_messages(state.channel_id, ids=batch)
            except Exception as e:
                logger.error(f"Map batch error: {e}")
                await asyncio.sleep(2)
                try:
                    msgs = await bot.get_messages(state.channel_id, ids=batch)
                except:
                    empty += len(batch)
                    continue

            for msg in msgs:
                if msg is None:
                    empty += 1
                    continue
                fetched += 1
                caption = msg.message or ""
                if caption:
                    opid = extract_opid_from_caption(caption)
                    if opid:
                        opid_to_msg_id[opid] = msg.id

            done = fetched + empty
            if done - last_progress >= 300:
                last_progress = done
                pct = (done / total) * 100
                try:
                    await status_msg.edit(
                        f"⏳ **[MAP] Scanning...**\n"
                        f"Progress: **{done}/{total}** ({pct:.0f}%)\n"
                        f"OPIDs mapped: **{len(opid_to_msg_id)}**"
                    )
                except:
                    pass

            if i % 500 == 0 and i > 0:
                await asyncio.sleep(0.3)

    except Exception as e:
        await event.respond(f"❌ Map scan error: `{e}`")
        return

    # ========================================
    # BUILD NEW TXT FILE WITH REPLACED LINKS
    # ========================================
    short_id = state.channel_id_short  # e.g., "249495393"
    mapped_count = 0
    not_found_count = 0
    not_found_lines = []

    new_lines = []

    for line_data in state.txt_file_lines:
        line = line_data['line']
        opid = line_data['opid']
        line_num = line_data['num']

        if not line:
            # Empty line, keep as-is
            new_lines.append("")
            continue

        if opid and opid in opid_to_msg_id:
            # Found! Replace the URL with channel message link
            msg_id = opid_to_msg_id[opid]
            new_link = f"https://t.me/c/{short_id}/{msg_id}"
            new_line = replace_line_link(line, new_link)
            new_lines.append(new_line)
            mapped_count += 1

        elif opid and opid not in opid_to_msg_id:
            # OPID exists in line but not found in channel
            new_lines.append(line)  # Keep original
            not_found_count += 1
            not_found_lines.append(f"Line {line_num}: {opid[:30]}...")

        else:
            # No OPID in this line, keep as-is
            new_lines.append(line)

    # Write output file
    output_content = "\n".join(new_lines)
    out_fname = f"mapped_{event.sender_id}.txt"

    with open(out_fname, 'w', encoding='utf-8') as f:
        f.write(output_content)

    # Summary
    report = (
        f"✅ **[MAP] Complete!**\n\n"
        f"📊 **Summary:**\n"
        f"├ Messages scanned: **{fetched}**\n"
        f"├ OPIDs in file: **{len(state.txt_opid_order)}**\n"
        f"├ OPIDs in channel: **{len(opid_to_msg_id)}**\n"
        f"├ ✅ Mapped: **{mapped_count}**\n"
        f"└ ❌ Not found: **{not_found_count}**\n\n"
        f"🔗 Links format: `https://t.me/c/{short_id}/MSG_ID`"
    )

    if not_found_lines:
        nf_display = "\n".join(not_found_lines[:20])
        if len(not_found_lines) > 20:
            nf_display += f"\n... +{len(not_found_lines) - 20} more"
        report += f"\n\n⚠️ **Not found in channel:**\n{nf_display}"

    await status_msg.edit(report)

    await bot.send_file(
        event.chat_id,
        out_fname,
        caption=(
            f"🗺️ **Mapped File**\n"
            f"✅ Mapped: **{mapped_count}** | ❌ Not found: **{not_found_count}**"
        )
    )

    if os.path.exists(out_fname):
        os.remove(out_fname)


# ===== FORWARD LOGIC =====

async def do_forward(event, state, status_msg):
    """
    Phase 1: Scan source messages, map OPID -> message_id
    Phase 2: Forward to target in .txt file order without forward tag
    """

    # PHASE 1: BUILD OPID -> MESSAGE_ID MAP
    opid_to_msg_id = {}
    fetched = 0
    empty = 0
    total = state.end_msg_id - state.start_msg_id + 1
    BATCH = 100
    all_ids = list(range(state.start_msg_id, state.end_msg_id + 1))
    last_progress = 0

    try:
        for i in range(0, len(all_ids), BATCH):
            batch = all_ids[i:i + BATCH]

            try:
                msgs = await bot.get_messages(state.channel_id, ids=batch)
            except Exception as e:
                logger.error(f"Phase1 batch error: {e}")
                await asyncio.sleep(2)
                try:
                    msgs = await bot.get_messages(state.channel_id, ids=batch)
                except:
                    empty += len(batch)
                    continue

            for msg in msgs:
                if msg is None:
                    empty += 1
                    continue
                fetched += 1
                caption = msg.message or ""
                if caption:
                    opid = extract_opid_from_caption(caption)
                    if opid:
                        opid_to_msg_id[opid] = msg.id

            done = fetched + empty
            if done - last_progress >= 300:
                last_progress = done
                pct = (done / total) * 100
                try:
                    await status_msg.edit(
                        f"⏳ **[FORWARD] Phase 1: Scanning...**\n"
                        f"Progress: **{done}/{total}** ({pct:.0f}%)\n"
                        f"OPIDs mapped: **{len(opid_to_msg_id)}**"
                    )
                except:
                    pass

            if i % 500 == 0 and i > 0:
                await asyncio.sleep(0.3)

    except Exception as e:
        await event.respond(f"❌ Phase 1 Error: `{e}`")
        return

    # MATCH
    txt_opids = state.txt_opid_order
    matched = []
    not_found = []

    for idx, opid in enumerate(txt_opids):
        if opid in opid_to_msg_id:
            matched.append((idx, opid, opid_to_msg_id[opid]))
        else:
            not_found.append((idx, opid))

    await status_msg.edit(
        f"✅ **[FORWARD] Phase 1 Complete!**\n\n"
        f"📊 Scanned: **{fetched}** msgs\n"
        f"🔑 File OPIDs: **{len(txt_opids)}**\n"
        f"🔑 Channel OPIDs: **{len(opid_to_msg_id)}**\n"
        f"✅ Matched: **{len(matched)}**\n"
        f"❌ Not found: **{len(not_found)}**\n\n"
        f"⏳ **Phase 2: Forwarding {len(matched)} messages...**\n"
        f"Target: `{state.target_channel_id}`"
    )

    if not matched:
        await event.respond("❌ No matching OPIDs! Nothing to forward.")
        return

    # PHASE 2: PRE-FETCH ALL NEEDED MESSAGES
    needed_msg_ids = [m[2] for m in matched]
    msg_cache = {}

    await asyncio.sleep(1)

    for i in range(0, len(needed_msg_ids), BATCH):
        batch_ids = needed_msg_ids[i:i + BATCH]
        try:
            msgs = await bot.get_messages(state.channel_id, ids=batch_ids)
            for msg in msgs:
                if msg and msg.id:
                    msg_cache[msg.id] = msg
        except Exception as e:
            logger.error(f"Cache fetch error: {e}")
            for mid in batch_ids:
                try:
                    msg = await bot.get_messages(state.channel_id, ids=mid)
                    if msg:
                        msg_cache[mid] = msg
                except:
                    pass

        if i % 500 == 0 and i > 0:
            await asyncio.sleep(0.3)

    try:
        await status_msg.edit(
            f"⏳ **[FORWARD] Phase 2: Sending...**\n"
            f"📦 Cached: **{len(msg_cache)}** msgs\n"
            f"🎯 Target: `{state.target_channel_id}`\n"
            f"Sending **{len(matched)}** in .txt order..."
        )
    except:
        pass

    # SEND IN TXT ORDER
    forwarded = 0
    failed = 0
    total_to_forward = len(matched)
    last_progress2 = 0

    for order_idx, (txt_idx, opid, src_msg_id) in enumerate(matched):
        try:
            src_msg = msg_cache.get(src_msg_id)
            if not src_msg:
                try:
                    src_msg = await bot.get_messages(state.channel_id, ids=src_msg_id)
                except:
                    pass

            if not src_msg:
                failed += 1
                continue

            caption = src_msg.message or ""

            if src_msg.media:
                await bot.send_file(
                    state.target_channel_id,
                    file=src_msg.media,
                    caption=caption,
                    formatting_entities=src_msg.entities,
                    silent=True
                )
            elif caption:
                await bot.send_message(
                    state.target_channel_id,
                    caption,
                    formatting_entities=src_msg.entities,
                    silent=True
                )
            else:
                failed += 1
                continue

            forwarded += 1

            if forwarded - last_progress2 >= 10 or forwarded == total_to_forward:
                last_progress2 = forwarded
                pct = (forwarded / total_to_forward) * 100
                try:
                    await status_msg.edit(
                        f"⏳ **[FORWARD] Phase 2: Sending...**\n"
                        f"Progress: **{forwarded}/{total_to_forward}** ({pct:.0f}%)\n"
                        f"✅ Sent: **{forwarded}** | ❌ Failed: **{failed}**"
                    )
                except:
                    pass

            await asyncio.sleep(3)

        except Exception as e:
            failed += 1
            error_str = str(e)
            logger.error(f"Forward error msg {src_msg_id}: {error_str}")

            if 'flood' in error_str.lower():
                wait_match = re.search(r'(\d+)\s*seconds?', error_str)
                wait_time = int(wait_match.group(1)) if wait_match else 30
                try:
                    await status_msg.edit(
                        f"⚠️ **Flood wait! Sleeping {wait_time}s...**\n"
                        f"Progress: {forwarded}/{total_to_forward}"
                    )
                except:
                    pass
                await asyncio.sleep(wait_time + 2)

                # Retry
                try:
                    src_msg = msg_cache.get(src_msg_id)
                    if src_msg and src_msg.media:
                        await bot.send_file(
                            state.target_channel_id,
                            file=src_msg.media,
                            caption=src_msg.message or "",
                            formatting_entities=src_msg.entities,
                            silent=True
                        )
                        forwarded += 1
                        failed -= 1
                    elif src_msg and src_msg.message:
                        await bot.send_message(
                            state.target_channel_id,
                            src_msg.message,
                            formatting_entities=src_msg.entities,
                            silent=True
                        )
                        forwarded += 1
                        failed -= 1
                except:
                    pass
            else:
                await asyncio.sleep(5)

    # FINAL REPORT
    report = (
        f"✅ **[FORWARD] Complete!**\n\n"
        f"📊 **Summary:**\n"
        f"├ Source scanned: **{fetched}** msgs\n"
        f"├ OPIDs in file: **{len(txt_opids)}**\n"
        f"├ Matched: **{len(matched)}**\n"
        f"├ ✅ Forwarded: **{forwarded}**\n"
        f"├ ❌ Failed: **{failed}**\n"
        f"└ ⚠️ Not in channel: **{len(not_found)}**\n\n"
        f"🎯 Target: `{state.target_channel_id}`"
    )

    if not_found:
        nf_lines = []
        for idx, opid in not_found[:30]:
            line_data = state.txt_file_opids.get(opid, {})
            line_num = line_data.get('line_number', '?')
            nf_lines.append(f"  Line {line_num}: `{opid[:30]}...`")
        nf_text = "\n".join(nf_lines)
        if len(not_found) > 30:
            nf_text += f"\n  ... +{len(not_found) - 30} more"
        report += f"\n\n⚠️ **Not found in source:**\n{nf_text}"

    if not_found:
        nf_entries = []
        for idx, opid in not_found:
            data = state.txt_file_opids.get(opid, {})
            if data.get('full_line'):
                nf_entries.append((data['line_number'], data['full_line']))

        if nf_entries:
            nf_entries.sort(key=lambda x: x[0])
            nf_content = "\n".join(e[1] for e in nf_entries)
            nf_fname = f"not_found_{event.sender_id}.txt"
            with open(nf_fname, 'w', encoding='utf-8') as f:
                f.write(nf_content)
            await status_msg.edit(report)
            await bot.send_file(
                event.chat_id,
                nf_fname,
                caption=f"⚠️ **{len(not_found)} OPIDs** not found in source channel."
            )
            if os.path.exists(nf_fname):
                os.remove(nf_fname)
        else:
            await status_msg.edit(report)
    else:
        await status_msg.edit(report)


# ===== START BOT =====
print("=" * 50)
print("🤖 OPID Bot Starting...")
print("Commands: /start /check /forward /map /cancel")
print("=" * 50)
bot.run_until_disconnected()
