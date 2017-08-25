#!/usr/bin/env python3

"""
Update the S3 bucket with new config files and assets.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time

import boto3


# Types of assets
ASSET_TYPES = {
    'json': ['.json'],
    'image': ['.png', '.gif', '.jpg'],
    'text': ['.txt'],
}


def build_empty_config():
    """
    Get a basic empty config. For consistency.

    :rtype:
        `dict`
    """
    return {
        'files': [],
        'lastUpdatedAt': int(time.time())
    }


def get_all_assets(asset_dir):
    """
    Get all available asset names in the base directory and the subdirectory they are in.
    First item in tuple is the asset directory, second is the asset name.

    :param asset_dir:
        Base directory to begin search from
    :type asset_dir:
        `str`
    :rtype:
        `list` of (`str`, `str`)
    """
    assets = []
    directories = []

    for file in os.listdir(asset_dir):
        file_path = os.path.join(asset_dir, file)
        if os.path.isfile(file_path):
            if not file.startswith('.') and 'config' not in file_path:
                assets.append((asset_dir, file))
        else:
            directories.append(file_path)
    for directory in directories:
        assets += get_all_assets(directory)
    assets.sort(key=lambda s: s[1])
    return assets


def get_asset_type(asset_name):
    """
    Gets the asset type from ASSET_TYPES of an asset given its name.

    :param asset_name:
        Name of the asset
    :type: asset_name:
        `str`
    :rtype:
        `str` or None
    """
    filetype = asset_name[asset_name.rfind('.'):].lower()
    for asset_type in ASSET_TYPES:
        if filetype in ASSET_TYPES[asset_type]:
            return asset_type
    return None


def build_dev_config(asset_dir, output_dir, filename):
    """
    Builds a config for a dev environment.

    :param asset_dir:
        Location of assets in filesystem
    :type asset_dir:
        `str`
    :param output_dir:
        Output location for config file
    :type output_dir:
        `str`
    :param filename:
        Output filename for config file
    :type filename:
        `str`
    """
    assets = get_all_assets(asset_dir)
    print('Retrieved {0} assets'.format(len(assets)))

    print('Creating output directory `{0}`'.format(output_dir))
    os.makedirs(output_dir)
    config = build_empty_config()

    for asset in assets:
        asset_folder = asset[0]
        asset_name = asset[1]
        asset_type = get_asset_type(asset[1])
        asset_zurl_exists = os.path.exists(os.path.join(asset_folder, '{}.gz'.format(asset_name)))
        file = {
            'name': '/{}'.format(asset_name),
            'size': os.path.getsize(os.path.join(asset_folder, asset_name)),
            'type': asset_type,
            'url': 'http://localhost:8080/{0}/{1}'.format(asset_type, asset_name),
            'version': 1,
        }

        if asset_zurl_exists:
            file['zurl'] = 'http://localhost:8080/{0}/{1}'.format(
                asset_type,
                '{}.gz'.format(asset_name)
            )
            file['zsize'] = os.path.getsize(os.path.join(asset_folder, '{}.gz'.format(asset_name)))

        config['files'].append(file)

    print('Dumping config to `{0}/{1}`'.format(output_dir, filename))
    with open(os.path.join(output_dir, filename), 'w') as config_file:
        json.dump(config_json, file, sort_keys=True, ensure_ascii=False, indent=2)


def get_most_recent_config(bucket):
    """
    Given an S3 bucket, find the most recent config file version in that bucket and return its
    version as an array of 3 integers. If no config files are found, returns [0, 0, 0].

    :param bucket:
        the S3 bucket to examine
    :type bucket:
        :class:S3.Bucket
    :rtype:
        `list` of `int`
    """
    objects = bucket.objects.all()
    max_version = [0, 0, 0]
    for item in objects:
        if item.key[:7] != 'config/' or len(item.key) <= 7:
            continue
        item_version = list(map(int, item.key.split('/')[1].split('.')[:3]))
        for element in range(len(item_version)):
            if item_version[element] > max_version[element]:
                max_version = item_version
                break
    print('Found most recent config version: {0}'.format(max_version))
    return max_version


def get_release_config_version(bucket, version):
    """
    Gets a string for the config version to build.

    :param bucket:
        the s3 bucket to examine for the most recent config version, if necessary
    :type bucket:
        :class:S3.Bucket
    :param version:
        Either the major.minor.patch build number for the config, or
        'major', 'minor', or 'patch' to update from the most recent config version
    :type version:
        `str`
    """
    if re.match(r'[0-9]+[.][0-9]+[.][0-9]+', version):
        return version

    last_version = get_most_recent_config(bucket)
    if version == 'major':
        last_version[0] = last_version[0] + 1
        last_version[1] = 0
        last_version[2] = 0
    elif version == 'minor':
        last_version[1] = last_version[1] + 1
        last_version[2] = 0
    elif version == 'patch':
        last_version[2] = last_version[2] + 1
    else:
        raise ValueError('`version` must be one of "major", "minor", "patch", or match "X.Y.Z"')

    last_version = [str(x) for x in last_version]
    return '.'.join(last_version)


def update_asset(
        bucket,
        name,
        asset_type,
        content,
        version,
        zcontent=None,
        compatible=False,
        configs={},
        upload_file=True):
    """
    Upload an asset to S3 bucket. Override existing versions, and update any config files
    that contain the asset. Returns the URL to access the asset.

    :param bucket:
        S3 bucket to upload to
    :type bucket:
        :class:S3.Bucket
    :param dir:
        Directory containing asset
    :type dir:
        `str`
    :param name:
        Filename of the asset
    :type name:
        `str`
    :param asset_type:
        Type of the asset
    :type asset_type:
        `str`
    :param content:
        Content of the asset
    :type content:
        `str`
    :param version:
        Version number for asset
    :type version:
        `int`
    :param zcontent:
        Zipped content of the asset
    :type zcontent:
        `str`
    :param compatible:
        If True, then previous configs will be checked if they contain the file and their
        versions updated
    :type compatible:
        `bool`
    :param configs:
        List of existing configs to check and update
    :type:
        `list` of `json`
    :param upload_file:
        True to upload the file, false to skip
    :type upload_file:
        `bool`
    :rtype:
        `str`
    """
    global S3
    global REGION

    content_type = 'application/json; charset=utf-8'
    if asset_type == 'image':
        if name[-3:] == 'png':
            content_type = 'image/png'
        elif name[-3:] == 'jpg':
            content_type = 'image/jpeg'
        elif name[-3:] == 'gif':
            content_type = 'image/gif'
    elif asset_type == 'text':
        content_type = 'text/plain; charset=utf-8'

    object_kwargs = {
        'ACL': 'public-read',
        'ContentType': content_type,
        'Metadata': {
            'version': str(version),
        },
    }

    if upload_file:
        print('Uploading asset `{0}`'.format('assets{0}'.format(name)))
        bucket.put_object(Key='assets{0}'.format(name), Body=content, **object_kwargs)

    base_object = S3.Object(bucket.name, 'assets{0}'.format(name)).get()
    size = base_object['ContentLength']
    version = int(base_object['Metadata']['version'])
    url = 'https://s3.{0}.amazonaws.com/{1}/assets{2}?versionId={3}'.format(
        REGION,
        bucket.name,
        name,
        base_object['VersionId']
    )

    asset = {
        'size': size,
        'url': url,
        'version': version,
    }

    if zcontent:
        if upload_file:
            print('Uploading asset `{0}`'.format('assets{0}.gz'.format(name)))
            bucket.put_object(
                Key='assets{0}.gz'.format(name),
                Body=zcontent,
                ContentEncoding='gzip',
                **object_kwargs
            )
        zipped_object = S3.Object(bucket.name, 'assets{0}.gz'.format(name)).get()
        asset['zsize'] = zipped_object['ContentLength']
        asset['zurl'] = 'https://s3.{0}.amazonaws.com/{1}/assets{2}.gz?versionId={3}'.format(
            REGION,
            bucket.name,
            name,
            zipped_object['VersionId']
        )

    if compatible:
        for config in configs:
            updated = False
            for file in configs[config]['content']['files']:
                if file['name'] != name or file['version'] != version - 1:
                    continue
                file['size'] = asset['size']
                file['url'] = asset['url']
                file['version'] = version
                if 'zsize' in file:
                    if 'zsize' in asset:
                        file['zsize'] = asset['zsize']
                        file['zurl'] = asset['zurl']
                    else:
                        file.pop('zsize', None)
                        file.pop('zurl', None)
                updated = True
            if updated:
                configs[config]['updated'] = True
                configs[config]['content']['lastUpdatedAt'] = int(time.time())
    return asset


def parse_existing_config(item, existing_configs):
    """
    Parse the content of a config and add it to the existing configs.

    :param item:
        An object from S3
    :type item:
        :class:S3.Object
    :param existing_configs:
        The existing configs
    :type existing_configs:
        `dict`
    """
    item_key = item.key
    existing_config = item.get()
    existing_configs[item_key] = {
        'content': json.loads(existing_config['Body'].read()),
        'key': item_key,
        'updated': False,
    }
    print('Parsed existing config `{0}`'.format(item_key))


def parse_existing_asset(item, existing_assets):
    """
    Parse the content of an asset and add it to the existing assets.

    :param item:
        An object from S3
    :type item:
        :class:S3.Object
    :param existing_assets:
        The existing assets
    :type existing_assets:
        `dict`
    """
    item_key = item.key[6:]
    if item_key[-3:] == '.gz':
        item_key = item_key[:-3]
        existing_assets[item_key]['zipped'] = True
        return

    existing_asset = item.get()
    existing_assets[item_key] = {
        'content': existing_asset['Body'].read(),
        'version': existing_asset['Metadata']['version'],
        'versionId': existing_asset['VersionId'],
        'zipped': False,
    }
    print('Parsed existing asset `{0}`'.format(item_key))


def update_changed_assets(bucket, asset_dir, output_dir, only, compatible=False):
    """
    Update assets which have changed from those versions already in the bucket. Also upload new
    assets not yet in the bucket. Returns a dict with updated assets and a dict of configs which
    may or may not have been updated due to the new assets.

    :param bucket:
        An S3 bucket to retrieve existing assets and configs from
    :type bucket:
        :class:S3.Bucket
    :param asset_dir:
        Asset directory
    :type asset_dir:
        `str`
    :param output_dir:
        Output directory for minified assets and config
    :type output_dir:
        `str`
    :param only:
        Set of asset names which should be updated, and all others skipped, or None.
    :type only:
        `set`
    :param compatible:
        If True, update existing configs to accept the new version.
    :type compatible:
        `bool`
    :rtype:
        `dict`, `dict`
    """
    # Minify and uglify assets
    print('Cleaning output directory `{0}'.format(output_dir))
    shutil.rmtree(output_dir)
    print('Beginning uglifyjs subprocess, from `{0}` to `{1}`'.format(asset_dir, output_dir))
    subprocess.run(['./script/uglify.sh', asset_dir, output_dir])

    # Get existing assets from bucket
    bucket_objects = bucket.objects.all()
    existing_assets = {}
    existing_configs = {}
    for item in bucket_objects:
        if item.key[:7] == 'config/' and len(item.key) > 7:
            parse_existing_config(item, existing_configs)
        elif item.key[:7] == 'assets/' and len(item.key) > 7:
            parse_existing_asset(item, existing_assets)

    # Get local assets and filter for only those specified to be updated
    assets = get_all_assets(output_dir)
    assets = [x for x in assets if only is None or '/{}'.format(x[1]) in only]
    print('Retrieved {0} assets'.format(len(assets)))

    updated_assets = {}
    for asset in assets:
        asset_folder = asset[0]
        asset_name = asset[1]
        slash_asset_name = '/{}'.format(asset_name)
        asset_type = get_asset_type(asset[1])

        if asset_name[-3:] == '.gz':
            continue

        last_version = 0
        asset_content = None
        asset_zcontent = None
        upload_file = True
        with open(os.path.join(asset_folder, asset_name), 'rb') as asset_file:
            asset_content = asset_file.read()
        if os.path.exists(os.path.join(asset_folder, '{}.gz'.format(asset_name))):
            with open(os.path.join(asset_folder, '{}.gz'.format(asset_name)), 'rb') as asset_zfile:
                asset_zcontent = asset_zfile.read()
        if slash_asset_name in existing_assets:
            if existing_assets[slash_asset_name]['content'] == asset_content:
                upload_file = False
            else:
                last_version = int(existing_assets[slash_asset_name]['version'])

        asset_details = update_asset(
            bucket,
            slash_asset_name,
            asset_type,
            asset_content,
            last_version + 1,
            zcontent=asset_zcontent,
            compatible=compatible,
            configs=existing_configs,
            upload_file=upload_file
        )
        built_asset = {
            'name': slash_asset_name,
            'size': asset_details['size'],
            'type': asset_type,
            'url': asset_details['url'],
            'version': asset_details['version'],
        }

        if 'zurl' in asset_details and 'zsize' in asset_details:
            built_asset['zsize'] = asset_details['zsize']
            built_asset['zurl'] = asset_details['zurl']

        updated_assets[slash_asset_name] = built_asset

    return updated_assets, existing_configs


def build_release_config(bucket, assets, version):
    """
    Build a config for release.

    :param bucket:
        An S3 bucket to retrieve existing assets and configs from
    :type bucket:
        :class:S3.Bucket
    :param assets:
        Asset names and details for the config
    :type assets:
        `dict`
    :param version:
        Version for config
    :type version:
        `int`
    :rtype:
        `str`, `dict`
    """
    config = build_empty_config()
    for asset in assets:
        config['files'].append(assets[asset])
    config_key = 'config/{0}.json'.format(version)
    config_details = {
        'content': config,
        'key': config_key,
        'updated': True,
    }
    print('Built config file `{0}`'.format(config_key))
    return config_key, config_details


def update_changed_configs(bucket, configs):
    """
    Update only config files in `configs` which have the key 'updated' set to True.

    :param bucket:
        S3 bucket which all configs exist in
    :type bucket:
        :class:S3.Bucket
    :param configs:
        Dictionary of config names and details
    :type configs:
        `dict`
    """
    for config in configs:
        if not configs[config]['updated']:
            continue
        print('Uploading config `{0}`'.format(configs[config]['key']))
        bucket.put_object(
            Key=configs[config]['key'],
            Body=json.dumps(configs[config]['content']),
            ACL='public-read'
        )


# Input validation
if len(sys.argv) >= 2 and sys.argv[1] == '--dev':
    DEV_ASSET_DIR = '../assets_dev/' if len(sys.argv) < 3 else sys.argv[2]
    DEV_OUTPUT_DIR = '../assets_dev/config' if len(sys.argv) < 4 else sys.argv[3]
    DEV_FILENAME = '*.json' if len(sys.argv) < 5 else sys.argv[4]
    build_dev_config(DEV_ASSET_DIR, DEV_OUTPUT_DIR, DEV_FILENAME)
    exit()
elif len(sys.argv) < 5:
    print('\n\tCampus Guide - Release Manager')
    print('\tUsage:   release_manager.py', end='')
    print(' <bucket_name> <asset_dir> <output_dir> <#.#.#|major|minor|patch> [options]')
    print('\tAlt:     release_manager.py', end='')
    print(' --dev <asset_dir>')
    print('\tExample: release_manager.py', end='')
    print(' <bucket_name> assets/ assets_release/ patch [options]')
    print('\tOptions:')
    print('\t--dev\t\t\t\tBuild a config file for dev based on the given directory')
    print('\t--no-new-config\t\t\tPush changed assets and only update configs which exist')
    print('\t--only <name1,...>\tUpdate only assets with the given names. Otherwise, update all')
    print('\t--region <region>\t\tAWS region')
    print('\t--compatible\t\t\tSpecify that assets changed are compatible with existing configs')
    print()
    exit()

# Parse arguments
BUCKET_NAME = sys.argv[1]
ASSET_DIR = sys.argv[2]
OUTPUT_DIR = sys.argv[3]
NEW_VERSION = sys.argv[4]
BUILD_CONFIG = True
REGION = 'ca-central-1'
ONLY_UPGRADE = None
COMPATIBLE = False

SKIP_NEXT = False
if len(sys.argv) > 5:
    for (index, arg) in enumerate(sys.argv[5:]):
        if SKIP_NEXT:
            SKIP_NEXT = False
            continue

        if arg == '--only':
            SKIP_NEXT = True
            ONLY_UPGRADE = set()
            for asset in sys.argv[index + 1].split(','):
                ONLY_UPGRADE.add(asset)
        elif arg == '--region':
            SKIP_NEXT = True
            REGION = sys.argv[index + 1]
        elif arg == '--no-new-config':
            BUILD_CONFIG = False
        elif arg == '--compatible':
            COMPATIBLE = True

S3 = boto3.resource('s3')
BUCKET = S3.Bucket(BUCKET_NAME)

updated_assets, updated_configs = update_changed_assets(
    BUCKET, ASSET_DIR, OUTPUT_DIR, ONLY_UPGRADE, compatible=COMPATIBLE)

if COMPATIBLE:
    update_changed_configs(BUCKET, updated_configs)
if BUILD_CONFIG:
    CONFIG_VERSION = get_release_config_version(BUCKET, NEW_VERSION)
    config_key, config_details = build_release_config(BUCKET, updated_assets, CONFIG_VERSION)
    update_changed_configs(BUCKET, {config_key: config_details})
