import os
import sys
import fnmatch
import time
import numpy as np
from sloth.core.exceptions import \
    ImproperlyConfigured, NotImplementedException, InvalidArgumentException
from sloth.core.utils import import_callable
import logging
LOG = logging.getLogger(__name__)

try:
    import cPickle as pickle
except:
    import pickle
try:
    import json
except:
    pass
try:
    import yaml
except:
    pass
try:
    import okapy
    import okapy.videoio as okv
    _use_pil = False
except:
    try:
        from PIL import Image
        _use_pil = True
    except:
        raise RuntimeError("Could neither find PIL nor okapy.  Sloth needs one of them for loading images.")


class AnnotationContainerFactory:
    def __init__(self, containers):
        """
        Initialize the factory with the mappings between file pattern and
        the container.

        Parameters
        ==========
        containers: tuple of tuples (str, str/class)
            The mapping between file pattern and container class responsible
            for loading/saving.
        """
        self._containers = []
        for pattern, item in containers:
            if type(item) == str:
                item = import_callable(item)
            self._containers.append((pattern, item))

    def patterns(self):
        return [pattern for pattern, item in self._containers]

    def create(self, filename, *args, **kwargs):
        """
        Create a container for the filename.

        Parameters
        ==========
        filename: str
            Filename for which a matching container should be created.
        *args, **kwargs:
            Arguments passed to constructor of the container.
        """
        for pattern, container in self._containers:
            if fnmatch.fnmatch(filename, pattern):
                return container(*args, **kwargs)
        raise ImproperlyConfigured(
            "No container registered for filename %s" % filename
        )


class AnnotationContainer:
    """
    Annotation Container base class.
    """

    def __init__(self):
        self.clear()

    def filename(self):
        return self._filename

    def clear(self):
        self._annotations = [] # TODO Why isn't this used? Annotations are passed as parameters instead. Let's have encapsulation.
        self._filename = None
        self._video_cache = {}

    def load(self, filename):
        """
        Load the annotations.
        """
        if not filename:
            raise InvalidArgumentException("filename cannot be empty")
        self._filename = filename
        start = time.time()
        ann = self.parseFromFile(filename)
        diff = time.time() - start
        LOG.info("Loaded annotations from %s in %.2fs" % (filename, diff))
        return ann

    def parseFromFile(self, filename):
        """
        Read the annotations from disk. Must be implemented in the subclass.
        """
        raise NotImplementedException(
            "You need to implement parseFromFile() in your subclass " +
            "if you use the default implementation of " +
            "AnnotationContainer.load()"
        )

    def save(self, annotations, filename=""):
        """
        Save the annotations.
        """
        if not filename:
            filename = self.filename()
        self.serializeToFile(filename, annotations)
        self._filename = filename

    def serializeToFile(self, filename, annotations):
        """
        Serialize the annotations to disk. Must be implemented in the subclass.
        """
        raise NotImplementedException(
            "You need to implement serializeToFile() in your subclass " +
            "if you use the default implementation of " +
            "AnnotationContainer.save()"
        )

    def _fullpath(self, filename):
        """
        Calculate the fullpath to the file, assuming that
        the filename is given relative to the label file's
        directory.
        """
        if self.filename() is not None:
            basedir = os.path.dirname(self.filename())
            fullpath = os.path.join(basedir, filename)
        else:
            fullpath = filename
        return fullpath

    def loadImage(self, filename):
        """
        Load and return the image referenced to by the filename.  In the
        default implementation this will try to load the image from a path
        relative to the label file's directory.
        """
        fullpath = self._fullpath(filename)
        if not os.path.exists(fullpath):
            LOG.warn("Image file %s does not exist." % fullpath)
            return None

        if _use_pil:
            im = Image.open(fullpath)
            return np.asarray(im)
        else:
            return okapy.loadImage(fullpath)

    def loadFrame(self, filename, frame_number):
        """
        Load the video referenced to by the filename, and return frame
        ``frame_number``.  In the default implementation this will try to load
        the video from a path relative to the label files directory.
        """
        fullpath = str(self._fullpath(filename))
        if not os.path.exists(fullpath):
            LOG.warn("Video file %s does not exist." % fullpath)
            return None

        # get video source from cache or load from file
        if fullpath in self._video_cache:
            vidsrc = self._video_cache[fullpath]
        else:
            vidsrc = okv.createVideoSourceFromString(fullpath)
            vidsrc = okv.toRandomAccessVideoSource(vidsrc)
            self._video_cache[fullpath] = vidsrc

        # get requested frame
        vidsrc.getFrame(frame_number)
        return vidsrc.getImage()


class PickleContainer(AnnotationContainer):
    """
    Simple container which pickles the annotations to disk.
    """

    def parseFromFile(self, fname):
        """
        Overwritten to read pickle files.
        """
        f = open(fname, "rb")
        return pickle.load(f)

    def serializeToFile(self, fname, annotations):
        """
        Overwritten to write pickle files.
        """
        # TODO make all image filenames relative to the label file
        f = open(fname, "wb")
        pickle.dump(annotations, f)


class OkapiAnnotationContainer(AnnotationContainer):
    """
    Converts a AnnotationPropertiesMap to a dict
    """
    def convertAnnotationPropertiesMapToDict(self, properties):
        propdict = {}
        for k, v in properties.items():
            propdict[k] = v
        return propdict

    """
    Simple container which writes the annotations to disk using okapy.AnnotationContainer.
    """
    def parseFromFile(self, filename):
        """
        Overwritten to read Okapi::Annotation files.
        """
        container = okapy.AnnotationContainer()
        container.ReadFromFile(filename)

        annotations = []
        for f in container.files():
            fileitem = self.convertAnnotationPropertiesMapToDict(f.properties())
            fileitem['class'] = fileitem['type']
            del fileitem['type']
            if f.isImage():
                fileitem['annotations'] = []
                for annotation in f.annotations():
                    ann = self.convertAnnotationPropertiesMapToDict(annotation.properties())
                    fileitem['annotations'].append(ann)
            elif f.isVideo():
                fileitem['frames'] = []
                for frame in f.frames():
                    frameitem = self.convertAnnotationPropertiesMapToDict(frame.properties())
                    frameitem['annotations'] = []
                    for annotation in frame.annotations():
                        ann = self.convertAnnotationPropertiesMapToDict(annotation.properties())
                        frameitem['annotations'].append(ann)
                    fileitem['frames'].append(frameitem)
            annotations.append(fileitem)

        return annotations

    """
    Converts a dict to a AnnotationPropertiesMap
    """
    def convertDictToAnnotationPropertiesMap(self, annotation, propdict):
        for k, v in propdict.items():
            if k != 'annotations' or k != 'frames':
                annotation.set_str(k, str(v))
        return annotation

    def serializeToFile(self, fname, annotations):
        """
        Overwritten to write Okapi::Annotation files.
        """
        container = okapy.AnnotationContainer()

        for f in annotations:
            fileitem = okapy.AnnotationFileItem()
            if f.has_key('class'):
                f['type'] = f['class']
                del f['class']
            fileitem = self.convertDictToAnnotationPropertiesMap(fileitem, f)
            if fileitem.isImage():
                if f.has_key('annotations'):
                    for annotation in f['annotations']:
                        annoitem = okapy.AnnotationItem()
                        annoitem = self.convertDictToAnnotationPropertiesMap(annoitem, annotation)
                        fileitem.annotations().push_back(annoitem)
            elif fileitem.isVideo():
                if f.has_key('frames'):
                    for frame in f['frames']:
                        frameitem = okapy.AnnotationFrameItem()
                        frameitem = self.convertDictToAnnotationPropertiesMap(frameitem, frame)
                        if frame.has_key('annotations'):
                            for annotation in frame['annotations']:
                                annoitem = okapy.AnnotationItem()
                                annoitem = self.convertDictToAnnotationPropertiesMap(annoitem, annotation)
                                frameitem.annotations().push_back(annoitem)
                        fileitem.frames().push_back(frameitem)
            container.files().push_back(fileitem)

        # TODO make all image filenames relative to the label file
        container.WriteToFile(fname)


class JsonContainer(AnnotationContainer):
    """
    Simple container which writes the annotations to disk in JSON format.
    """

    def parseFromFile(self, fname):
        """
        Overwritten to read JSON files.
        """
        f = open(fname, "r")
        return json.load(f)

    def serializeToFile(self, fname, annotations):
        """
        Overwritten to write JSON files.
        """
        # TODO make all image filenames relative to the label file
        f = open(fname, "w")
        json.dump(annotations, f, indent=4)


class YamlContainer(AnnotationContainer):
    """
    Simple container which writes the annotations to disk in YAML format.
    """

    def parseFromFile(self, fname):
        """
        Overwritten to read YAML files.
        """
        f = open(fname, "r")
        return yaml.load(f)

    def serializeToFile(self, fname, annotations):
        """
        Overwritten to write YAML files.
        """
        # TODO make all image filenames relative to the label file
        f = open(fname, "w")
        yaml.dump(annotations, f)


class FileNameListContainer(AnnotationContainer):
    """
    Simple container to initialize the files to be annotated.
    """

    def parseFromFile(self, filename):
        self._basedir = os.path.dirname(filename)
        f = open(filename, "r")

        annotations = []
        for line in f:
            line = line.strip()
            fileitem = {
                'filename':    line,
                'class':        'image',
                'annotations': [],
            }
            annotations.append(fileitem)

        return annotations

    def serializeToFile(self, filename, annotations):
        raise NotImplemented(
                "FileNameListContainer.save() is not implemented yet.")


class FeretContainer(AnnotationContainer):
    """
    Container for Feret labels.
    """

    def parseFromFile(self, filename):
        """
        Overwritten to read Feret label files.
        """
        f = open(filename)

        annotations = []
        for line in f:
            s = line.split()
            fileitem = {
                'filename': s[0] + ".bmp",
                'class': 'image',
            }
            fileitem['annotations'] = [
                {'class': 'left_eye',
                 'x': int(s[1]), 'y': int(s[2])},
                {'class': 'right_eye',
                 'x': int(s[3]), 'y': int(s[4])},
                {'class': 'mouth',
                 'x': int(s[5]), 'y': int(s[6])}
            ]
            annotations.append(fileitem)

        return annotations

    def serializeToFile(self, filename, annotations):
        """
        Not implemented yet.
        """
        # TODO make sure the image paths are
        # relative to the label file's directory
        raise NotImplemented(
            "FeretContainer.serializeToFile() is not implemented yet."
        )
