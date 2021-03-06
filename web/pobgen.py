# -*- coding: utf-8 -*-
from __future__ import unicode_literals


import re
import itertools
import collections
import base64
import struct
import zlib
import json
import warnings

from lxml.builder import E
import lxml.etree
import nebuloch.names
from nebuloch.mods import Translator, CannotTranslateMod


tr = Translator('Traditional Chinese', '')


def get_encoded_tree(char, tree):
    head = [0, 0, 0, 4, char['classId'], char['ascendancyClass'], 0]
    return base64.urlsafe_b64encode(
        struct.pack(
            '>BBBBBBB{}H'.format(len(tree["hashes"])),
            *itertools.chain(head, tree['hashes']))
    ).decode('ascii')


def Tree(char, tree):
    sockets = []
    for id, item in enumerate(tree['items'], 1):
        x = item['x']
        sockets.append(
            E.Socket(
                nodeId=str(tree['jewel_slots'][x]),
                itemId=str(id)))

    return E.Tree(
        E.Spec(
            E.URL(
                'https://www.pathofexile.com/passive-skill-tree/' + get_encoded_tree(char, tree)
            ),
            E.Sockets(*sockets)
        ),
        activeSpec='1'
    )


def Gem(item):
    nameSpec = nebuloch.names.translate(item['typeLine']).replace(' Support', '')
    level = 20
    quality = 0
    for prop in item['properties']:
        if prop['name'] == '等級':
            level = int(prop['values'][0][0].replace('（最高等級）', ''))
        elif prop['name'] == '品質':
            quality = int(prop['values'][0][0].lstrip('+').rstrip('%'))
    return E.Gem(
        level=str(level),
        quality=str(quality),
        enabled='true',
        nameSpec=nameSpec,
    )


RARITY_MAP = {
    0: 'NORMAL',
    1: 'MAGIC',
    2: 'RARE',
    3: 'UNIQUE',
    9: 'RELIC'
}

SLOT_MAP = {
    'Amulet': 'Amulet',
    'Belt': 'Belt',
    'BodyArmour': 'Body Armour',
    'Boots': 'Boots',
    'Gloves': 'Gloves',
    'Helm': 'Helmet',
    'Offhand': 'Weapon 2',
    'Offhand2': 'Weapon 2 Swap',
    'Ring': 'Ring 1',
    'Ring2': 'Ring 2',
    'Weapon': 'Weapon 1',
    'Weapon2': 'Weapon 1 Swap'
}


def clean_name(name):
    return re.sub(r'\<\<set\:\w+\>\>', '', name)


def i_item_to_pob(item):
    rarity = RARITY_MAP[item['frameType']]
    yield 'Rarity: {}'.format(rarity)
    if rarity == 'RARE':
        itype = item['category']
        if not isinstance(itype, str):
            itype, isubtype = next(iter(itype.items()))
            if isubtype:
                itype = isubtype[0]
        yield '{} {} {}'.format(rarity, itype, item["id"][-7:])
    elif rarity in ('UNIQUE', 'RELIC'):
        yield nebuloch.names.translate(clean_name(item['name']))
    if rarity == 'MAGIC':
        twbase = clean_name(item['typeLine']).rpartition('精良的 ')[-1]
        parts = re.findall('([^的之]+[的之]?)', twbase)
        accumulated = ''
        for part in reversed(parts):
            accumulated = part + accumulated
            try:
                translated = nebuloch.names.translate(accumulated)
            except nebuloch.names.CannotTranslateName:
                continue
            yield '{} {} {}'.format(rarity, translated, item["id"][-7:])
            break
        else:
            raise nebuloch.names.CannotTranslateName(twbase)
    else:
        yield nebuloch.names.translate(item['typeLine'].rpartition('精良的 ')[-1])
    yield "Unique ID: {}".format(item['id'])
    yield "Item Level: {}".format(item['ilvl'])
    quality = 0
    radius = None
    for prop in item.get('properties', ()):
        if prop['name'] == '品質':
            quality = prop['values'][0][0].lstrip('+').rstrip('%')
        if prop['name'] == '範圍':
            radius = {'小': 'Small', '中': 'Medium', '大': 'Large'}[prop['values'][0][0]]
    yield 'Quality: {}'.format(quality)
    if radius is not None:
        yield 'Radius: {}'.format(radius)
    if 'sockets' in item:
        socketgroups = collections.defaultdict(list)
        for socket in item['sockets']:
            socketgroups[socket['group']].append(socket['sColour'])
        sockstr = ' '.join('-'.join(colors) for colors in socketgroups.values())
        yield 'Sockets: ' + sockstr
    if item.get('corrupted'):
        yield 'Corrupted'
    n_implicits = len(item.get('implicitMods', ())) + len(item.get('enchantMods', ()))
    yield 'Implicits: {}'.format(n_implicits)
    for mod in itertools.chain(
        item.get('implicitMods', ()),
        item.get('enchantMods', ()),
        item.get('explicitMods', ())
    ):
        yield tr(mod)
    for cmod in item.get('craftedMods', ()):
        yield '{crafted}' + tr(cmod)
    if item.get('shaper'):
        yield 'Shaper Item'
    if item.get('elder'):
        yield 'Elder Item'


def item_to_pob(item):
    return '\n'.join(i_item_to_pob(item))


def import_socketed_items(item, slot):
    """returns [skills], [abyss jewels]"""
    if 'socketedItems' not in item:
        return [], []
    groups = collections.defaultdict(list)
    jewels = []
    for socketedItem in item['socketedItems']:
        socket = item['sockets'][socketedItem['socket']]
        groupId = socket['group']
        socketColor = socket['sColour']
        if socketColor == 'A':  # Abyss jewel
            jewels.append(item_to_pob(socketedItem))
        else:
            groups[groupId].append(Gem(socketedItem))
    gems = [
        E.Skill(*gems, enabled='true', slot=slot, mainActiveSkillCalcs='nil', mainActiveSkill='nil')
        for gems in groups.values()
    ]
    return gems, jewels


# These are the categories that we are uncapable of handling at the moment
CATEGORY_BLACKLIST = set('gems currency maps cards monsters leaguestones'.split())

# These are the inventoryId's that are causing trouble
INVENTORY_BLACKLIST = set((
    # We are only importing equipped items, and quest items are currently
    # causing troubles, so we are ignoring them for now
    'MainInventory',

    'Map',  # The item is on Zana's Map Device
    'Cursor'  # The item is on the cursor
))


def ItemsSkills(items):
    item_list = []
    slot_list = []
    skill_list = []
    abyss_todo = []
    for id, item in enumerate(items, 1):
        strid = str(id)
        inventoryId = item['inventoryId']
        if next(iter(item['category'])) in CATEGORY_BLACKLIST:
            pass
        elif inventoryId in INVENTORY_BLACKLIST or inventoryId.endswith('MasterCrafting'):
            pass
        elif item['frameType'] not in RARITY_MAP:
            warnings.warn('frameType = {!r}, inventoryId = {!r}'.format(item['frameType'], inventoryId))
        else:
            pob = item_to_pob(item)
            item_list.append(E.Item(pob, id=strid))
            if inventoryId != 'PassiveJewels':
                if inventoryId == 'Flask':
                    slot = 'Flask {}'.format(item['x'] + 1)
                else:
                    slot = SLOT_MAP[inventoryId]
                slot_list.append(E.Slot(name=slot, itemId=strid))
                local_skills, abyss = import_socketed_items(item, slot)
                skill_list.extend(local_skills)
                if abyss:
                    abyss_todo.append((slot, abyss))
    for parent_slot, abyss_jewels in abyss_todo:
        for socknum, abyss_jewel in enumerate(abyss_jewels, 1):
            id += 1
            strid = str(id)
            item_list.append(E.Item(abyss_jewel, id=strid))
            slot = '%s Abyssal Socket %d' % (parent_slot, socknum)
            slot_list.append(E.Slot(name=slot, itemId=strid))
    return (
        E.Items(*(item_list + slot_list)),
        E.Skills(
            *skill_list,
            sortGemsByDPS='true'
        )
    )


def export(items, tree):
    char = items['character']
    items, skills = ItemsSkills(tree['items'] + items['items'])
    if not len(skills):
        L0 = ('nil', None)
    else:
        L0 = max(enumerate(skills, 1), key=lambda m: len(m[1]))
    defsock, maxlink_skill = L0
    pob = E.PathOfBuilding(
        E.Build(level=str(char['level']), targetVersion='3_0', mainSocketGroup=str(defsock)),
        skills,
        Tree(char, tree),
        items,
    )
    return base64.urlsafe_b64encode(zlib.compress(lxml.etree.tostring(pob))).decode('ascii')


def main():
    import sys
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('a')
    parser.add_argument('b')
    parser.add_argument('--poesessid')
    args = parser.parse_args()
    a = args.a
    b = args.b
    poesessid = args.poesessid
    if a.lower().endswith('.json') and b.lower().endswith('.json'):
        with open(a) as af, b as bf:
            items = json.load(af)
            tree = json.load(bf)
        print(export(items, tree))


if __name__ == '__main__':
    main()
