import sys
import os

# Point to the tool
sys.path.append(os.path.join(os.getcwd(), 'src/flight_analysis/pats'))

# Define your variable here
FILE_TO_RUN = "main2.py" 

# Now you can use it
import importlib
pats_tool = importlib.import_module(FILE_TO_RUN)
pats_tool.main()