# Copyright (c) 2012-2016 Seafile Ltd.
# coding:utf-8
import os
import posixpath
import timeit
import tempfile
import urllib2
import logging
from StringIO import StringIO

from seahub.base.templatetags.seahub_tags import email2nickname
from PIL import Image, ImageDraw, ImageFont
try:
    from moviepy.editor import VideoFileClip
    _ENABLE_VIDEO_THUMBNAIL = True
except ImportError:
    _ENABLE_VIDEO_THUMBNAIL = False
from seaserv import get_file_id_by_path, get_repo, get_file_size, \
    seafile_api

from seahub.utils import gen_inner_file_get_url, get_file_type_and_ext
from seahub.utils.file_types import VIDEO
from seahub.settings import THUMBNAIL_IMAGE_SIZE_LIMIT, \
    THUMBNAIL_EXTENSION, THUMBNAIL_ROOT, THUMBNAIL_IMAGE_ORIGINAL_SIZE_LIMIT,\
    ENABLE_VIDEO_THUMBNAIL, THUMBNAIL_VIDEO_FRAME_TIME

# Get an instance of a logger
logger = logging.getLogger(__name__)

if ENABLE_VIDEO_THUMBNAIL:
    try:
        from moviepy.editor import VideoFileClip
        logger.debug('Video thumbnail is enabled.')
    except ImportError:
        logger.error("Could not find moviepy installed.")
else:
    logger.debug('Video thumbnail is disabled.')

def get_thumbnail_src(repo_id, size, path):
    return posixpath.join("thumbnail", repo_id, str(size), path.lstrip('/'))

def get_share_link_thumbnail_src(token, size, path):
    return posixpath.join("thumbnail", token, str(size), path.lstrip('/'))

def get_rotated_image(image):

    # get image's exif info
    try:
        exif = image._getexif() if image._getexif() else {}
    except Exception:
        return image

    orientation = exif.get(0x0112) if isinstance(exif, dict) else 1
    # rotate image according to Orientation info

    # im.transpose(method)
    # Returns a flipped or rotated copy of an image.
    # Method can be one of the following: FLIP_LEFT_RIGHT, FLIP_TOP_BOTTOM, ROTATE_90, ROTATE_180, or ROTATE_270.

    # expand: Optional expansion flag.
    # If true, expands the output image to make it large enough to hold the entire rotated image.
    # If false or omitted, make the output image the same size as the input image.

    if orientation == 2:
        # Vertical image
        image = image.transpose(Image.FLIP_LEFT_RIGHT)
    elif orientation == 3:
        # Rotation 180
        image = image.rotate(180)
    elif orientation == 4:
        image = image.rotate(180).transpose(Image.FLIP_LEFT_RIGHT)
        # Horizontal image
    elif orientation == 5:
        # Horizontal image + Rotation 90 CCW
        image = image.rotate(-90, expand=True).transpose(Image.FLIP_LEFT_RIGHT)
    elif orientation == 6:
        # Rotation 270
        image = image.rotate(-90, expand=True)
    elif orientation == 7:
        # Horizontal image + Rotation 270
        image = image.rotate(90, expand=True).transpose(Image.FLIP_LEFT_RIGHT)
    elif orientation == 8:
        # Rotation 90
        image = image.rotate(90, expand=True)

    return image

def generate_thumbnail(request, repo_id, size, path, watermark=''):
    """ generate and save thumbnail if not exist

    before generate thumbnail, you should check:
    1. if repo exist: should exist;
    2. if repo is encrypted: not encrypted;
    3. if ENABLE_THUMBNAIL: enabled;
    """

    try:
        size = int(size)
    except ValueError as e:
        logger.error(e)
        return (False, 400)

    thumbnail_dir = os.path.join(THUMBNAIL_ROOT, str(size))
    if not os.path.exists(thumbnail_dir):
        os.makedirs(thumbnail_dir)

    file_id = get_file_id_by_path(repo_id, path)
    if not file_id:
        return (False, 400)

    thumbnail_file = get_thumbnail_file_path(THUMBNAIL_ROOT, file_id, size, watermark=watermark)
    if os.path.exists(thumbnail_file):
        return (True, 200)

    repo = get_repo(repo_id)
    file_size = get_file_size(repo.store_id, repo.version, file_id)
    filetype, fileext = get_file_type_and_ext(os.path.basename(path))

    if filetype == VIDEO:
        # video thumbnails
        if ENABLE_VIDEO_THUMBNAIL:
            return create_video_thumbnails(repo, file_id, path, size,
                                           thumbnail_file, file_size)
        else:
            return (False, 400)

    # image thumbnails
    if file_size > THUMBNAIL_IMAGE_SIZE_LIMIT * 1024**2:
        return (False, 400)

    token = seafile_api.get_fileserver_access_token(repo_id,
            file_id, 'view', '', use_onetime=True)

    if not token:
        return (False, 500)

    inner_path = gen_inner_file_get_url(token, os.path.basename(path))
    try:
        image_file = urllib2.urlopen(inner_path)
        f = StringIO(image_file.read())
        return _create_thumbnail_common(f, thumbnail_file, size, email=watermark)
    except Exception as e:
        logger.error(e)
        return (False, 500)

def create_video_thumbnails(repo, file_id, path, size, thumbnail_file, file_size):

    t1 = timeit.default_timer()
    token = seafile_api.get_fileserver_access_token(repo.id,
            file_id, 'view', '', use_onetime=False)

    if not token:
        return (False, 500)

    inner_path = gen_inner_file_get_url(token, os.path.basename(path))
    clip = VideoFileClip(inner_path)
    tmp_path = str(os.path.join(tempfile.gettempdir(), '%s.png' % file_id[:8]))

    clip.save_frame(tmp_path, t=THUMBNAIL_VIDEO_FRAME_TIME)
    t2 = timeit.default_timer()
    logger.debug('Create thumbnail of [%s](size: %s) takes: %s' % (path, file_size, (t2 - t1)))

    try:
        ret = _create_thumbnail_common(tmp_path, thumbnail_file, size)
        os.unlink(tmp_path)
        return ret
    except Exception as e:
        logger.error(e)
        os.unlink(tmp_path)
        return (False, 500)

def _create_thumbnail_common(fp, thumbnail_file, size, **kwargs):
    """Common logic for creating image thumbnail.

    `fp` can be a filename (string) or a file object.
    """
    image = Image.open(fp)

    # check image memory cost size limit
    # use RGBA as default mode(4x8-bit pixels, true colour with transparency mask)
    # every pixel will cost 4 byte in RGBA mode
    width, height = image.size
    image_memory_cost = width * height * 4 / 1024 / 1024
    if image_memory_cost > THUMBNAIL_IMAGE_ORIGINAL_SIZE_LIMIT:
        return (False, 403)

    if image.mode not in ["1", "L", "P", "RGB", "RGBA"]:
        image = image.convert("RGB")

    if kwargs['email']:
        image = add_text_to_image(image, email2nickname(kwargs['email']), kwargs['email'])
    else:
        image = get_rotated_image(image)
        image.thumbnail((size, size), Image.ANTIALIAS)
    image.save(thumbnail_file, THUMBNAIL_EXTENSION)
    return (True, 200)

def add_text_to_image(img, user, email):
    try:
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
    except Exception as e:
        logger.debug(e)
    copyImgsize = img.size[0] if img.size[0] < img.size[1] else img.size[1]
    font_size = (copyImgsize -200)/200*3 + 11

    #calc the background size
    font = ImageFont.truetype('seahub/thumbnail/font.ttc', font_size)
    test_overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    image_draw = ImageDraw.Draw(test_overlay)

    #calc the test size and position
    margin_ = (copyImgsize - 200)/ 200 * 1 + 5
    margin = [margin_, margin_]
    test_size_x, test_size_y = image_draw.textsize(user, font=font)
    test_size_email_x, test_size_email_y = image_draw.textsize(email, font=font)
    text_xy_user = (img.size[0] - test_size_x - margin[0], img.size[1] - test_size_y - margin[1])
    text_xy_email = (img.size[0] - test_size_email_x - margin[0], img.size[1] - 2 * test_size_email_y - margin[1])
    max_width = max(test_size_x,test_size_email_x)

    #draw the background of rect , and draw  the watermark
    image_draw.rectangle([ img.size[0] - max_width - 2 * margin[0] , img.size[1] - 2 * test_size_y - 2 * margin[1] , img.size[0] + margin[0], img.size[1] + margin[1] ], fill=(0, 0, 0, 88))
    image_draw.text(text_xy_user, user, font=font, fill=(255, 255, 245, 255))
    image_draw.text(text_xy_email, email, font=font, fill=(255, 255, 245, 255))
    image_width_text = Image.alpha_composite(img, test_overlay)
    return image_width_text

def get_thumbnail_file_path(root_dir, file_id, size, watermark=''):
    if  watermark:
        path = os.path.join(root_dir, str(size), file_id + '_' + watermark)
    else:
        path = os.path.join(root_dir, str(size), file_id)
    return path

