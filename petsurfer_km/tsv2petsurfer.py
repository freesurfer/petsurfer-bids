#!/usr/bin/env python3

import sys
import os
import csv
import argparse
import numpy as np

description = """
Convert tsv file (as created by petprep) to something petsurfer can use
"""
def main():

  # configure command-line
  parser = argparse.ArgumentParser(description=description)
  parser.add_argument("--tsv", help="TSV file")
  parser.add_argument("--o", help="output file")
  parser.add_argument("--frametime", action='store_true', help="output will be mean frame time")
  parser.add_argument("--roiavg", nargs="*", help="output will be mean frame time")
  parser.add_argument("--cblum", action='store_true', help="same as --roiavg {Left,Right}-Cerebellum-Cortex")
  parser.add_argument("--hb", action='store_true', help="Save output as high-binding region")
  parser.add_argument("--all", nargs="*", help="output will be tsv with all ROIs except those listed after --all (removes frame_{start,end} too)")

  # check for no arguments
  if len(sys.argv) < 2:
    parser.print_help()
    sys.exit(1)
    
  # print out the command line
  print(" ".join(sys.argv))

  # parse commandline
  args = parser.parse_args()

  if(args.tsv is None):
    print("ERROR: must spec TSV file with --tsv")
    sys.exit(1);
  if(args.o is None):
    print("ERROR: must spec output file with --o")
    sys.exit(1);

  if(args.cblum is True):
    args.roiavg = ["Left-Cerebellum-Cortex","Right-Cerebellum-Cortex"];

  if(args.roiavg is not None):
    if(len(args.roiavg)>0):
      print(args.roiavg)

  if(args.all is not None):
    if(len(args.all)>0):
      print(args.all)

  if(args.all is None and args.roiavg is None and args.frametime is False):
    print("ERROR: nothing to do, must spec --frametime, --roiavg, --cblum, or --all")
    sys.exit(1);

  filename, file_extension = os.path.splitext(args.tsv);
  if(file_extension == ".csv"): delimiter=",";
  if(file_extension == ".tsv"): delimiter="\t";
  tsv = csv.reader(open(args.tsv, "r"), delimiter=delimiter, quotechar='"')

  fields = next(tsv);
  #print(fields);

  data = [];  # Data rows
  nrows = 0;
  for row in tsv:
    #data.append(row)
    float_row = [float(item) for item in row]
    data.append(float_row)
    nrows = nrows + 1;

  print(nrows)
  indices = [];

  if(args.frametime):
    try:
      k = fields.index("frame_start");
    except:
      print("ERROR: cannot find frame_start in tsv file");
      sys.exit(1);
    indices.append(k);
    try:
      k = fields.index("frame_end");
    except:
      print("ERROR: cannot find frame_end in tsv file");
      sys.exit(1);
    #indices.append(k);

  if(args.roiavg is not None):
    for roiname in args.roiavg:
      try:
        k = fields.index(roiname);
      except:
        print("ERROR: cannot find %s in tsv file" % roiname);
        sys.exit(1);
      indices.append(k);

  if(args.frametime or args.roiavg is not None):
    print(indices)
    avg = [];
    for k in range(0,nrows):
      avgk = 0;
      for index in indices:
        avgk = avgk + data[k][index];
      avgk = avgk/len(indices);
      avg.append(avgk);
    try:
      print("Writing to %s" % args.o);
      with open(args.o, 'w') as file:
        if(args.hb): file.write("Frame HighBind\n")
        k = 0
        for item in avg:
          if(args.hb): file.write(f"{k} ")
          file.write(f"{item}\n")
          k = k + 1
    except IOError as e:
      print(f"Error writing to file: {e}")
      sys.exit(1);

  if(args.all is not None):
    xlist = args.all;
    #xlist.append("frame_start"); # Keep this as first column
    xlist.append("frame_end");
    # Remove these as they are not reliably there or not interesting
    xlist.append("Left-vessel");
    xlist.append("Right-vessel");
    xlist.append("CSF-ExtraCerebral");
    xlist.append("Head-ExtraCerebral");
    xlist.append("AirCavity");
    xlist.append("Optic-Chiasm");
    xlist.append("3rd-Ventricle");
    xlist.append("4th-Ventricle");
    print(xlist)
    try:
      print("Writing to %s" % args.o);
      with open(args.o, 'w') as file:
        indices = [];
        k = 0;
        for roi in fields:
          if(not(roi in xlist)):
            file.write(f"{roi}\t")
            indices.append(k);
          k=k+1;
        file.write("\n");
        for row in data:
          for index in indices:
            item = row[index];
            file.write(f"{item}\t")
          file.write("\n");
    except IOError as e:
      print(f"Error writing to file: {e}")
      sys.exit(1);


  print("tsv2petsurfer done");
  sys.exit(0)

if __name__ == "__main__":
    main()
