# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A demo which runs object classification on camera frames."""
import argparse
import time
import re
import svgwrite
import imp
import os

import collections
import operator
import numpy as np
from PIL import Image

from edgetpu.classification.engine import ClassificationEngine
import tflite_runtime.interpreter as tflite
import gstreamer

Class = collections.namedtuple('Class', ['id', 'score'])
EDGETPU_SHARED_LIB = 'libedgetpu.so.1'

def load_labels(path):
    p = re.compile(r'\s*(\d+)(.+)')
    with open(path, 'r', encoding='utf-8') as f:
       lines = (p.match(line).groups() for line in f.readlines())
       return {int(num): text.strip() for num, text in lines}

def generate_svg(dwg, text_lines):
    for y, line in enumerate(text_lines):
      dwg.add(dwg.text(line, insert=(11, y*20+1), fill='black', font_size='20'))
      dwg.add(dwg.text(line, insert=(10, y*20), fill='white', font_size='20'))

def make_interpreter(model_file):
  model_file, *device = model_file.split('@')
  return tflite.Interpreter(
     model_path=model_file,
      experimental_delegates=[
          tflite.load_delegate(EDGETPU_SHARED_LIB,
                               {'device': device[0]} if device else {})
      ])

def input_size(interpreter):
  """Returns input image size as (width, height) tuple."""
  _, height, width, _ = interpreter.get_input_details()[0]['shape']
  return width, height

def input_tensor(interpreter):
  """Returns input tensor view as numpy array of shape (height, width, 3)."""
  tensor_index = interpreter.get_input_details()[0]['index']
  return interpreter.tensor(tensor_index)()[0]

def output_tensor(interpreter):
  """Returns dequantized output tensor."""
  output_details = interpreter.get_output_details()[0]
  output_data = np.squeeze(interpreter.tensor(output_details['index'])())
  scale, zero_point = output_details['quantization']
  return scale * (output_data - zero_point)

def set_input(interpreter, data):
  """Copies data to input tensor."""
  input_tensor(interpreter)[:, :] = data

def get_output(interpreter, top_k, score_threshold):
  """Returns no more than top_k classes with score >= score_threshold."""
  scores = output_tensor(interpreter)
  classes = [
      Class(i, scores[i])
      for i in np.argpartition(scores, -top_k)[-top_k:]
      if scores[i] >= score_threshold
  ]
  return sorted(classes, key=operator.itemgetter(1), reverse=True)

def main():
    default_model_dir = "../all_models"
    default_model = 'mobilenet_v2_1.0_224_quant_edgetpu.tflite'
    default_labels = 'imagenet_labels.txt'
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', help='.tflite model path',
                        default=os.path.join(default_model_dir,default_model))
    parser.add_argument('--labels', help='label file path',
                        default=os.path.join(default_model_dir, default_labels))
    parser.add_argument('--top_k', type=int, default=3,
                        help='number of classes with highest score to display')
    parser.add_argument('--threshold', type=float, default=0.1,
                        help='class score threshold')
    args = parser.parse_args()

    print("Loading %s with %s labels."%(args.model, args.labels))
    engine = ClassificationEngine(args.model)
    labels = load_labels(args.labels)

    interpreter = make_interpreter(args.model)
    interpreter.allocate_tensors()

    last_time = time.monotonic()
    def user_callback(image, svg_canvas):
      nonlocal last_time
      start_time = time.monotonic()
      size = input_size(interpreter)
      image = image.resize((size), Image.NEAREST) #eventually move this into one nice function I think. resample=Image.NEAREST
      set_input(interpreter, image)
      interpreter.invoke()
      end_time = time.monotonic()
      results = get_output(interpreter, args.top_k, args.threshold)
      text_lines = [
          'Inference: %.2f ms' %((end_time - start_time) * 1000),
          'FPS: %.2f fps' %(1.0/(end_time - last_time)),
      ]
      for result in results:
          print('%s: %.5f' % (labels.get(result.id, result.id), result.score))
          text_lines.append('score=%.2f: %s' % (result.score, labels.get(result.id, result.id)))
      print(' '.join(text_lines))
      last_time = end_time
      generate_svg(svg_canvas, text_lines)

    result = gstreamer.run_pipeline(user_callback)

if __name__ == '__main__':
    main()
